import os
import sys
import site
import logging
import traceback
import json
import math
import time
import datetime as dt

import httpx2
from google import genai
from google.genai import errors, types

try:
    # google-genai 는 기본으로 httpx2 로 요청하고, GOOGLE_GENAI_HTTP_CLIENT=httpx 일 때만 httpx 를 쓴다 (httpx 는 선택 설치)
    import httpx
except ImportError:
    httpx = None

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

from src.services import gcs_upload_json
from src.services import gcs_download_json
from src.services import tweet_scrapper_post

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

# --- 1. 설정 ---

def load_api_key_from_config():
    """
    config.json 파일에서 API 키를 로드합니다.
    """
    config_path = os.path.join(pjt_home_path, 'config.json')
    try:
        with open(config_path, 'r', encoding='utf-8') as config_file:
            config = json.load(config_file)
            api_key = config.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("config.json 파일에 'GEMINI_API_KEY'가 없거나 값이 비어있습니다.")
            return api_key
    except FileNotFoundError:
        logger.error(f"오류: 설정 파일 '{config_path}'을(를) 찾을 수 없습니다.")
        return None
    except json.JSONDecodeError:
        logger.error(f"오류: '{config_path}' 파일이 올바른 JSON 형식이 아닙니다.")
        return None
    except ValueError as e:
        logger.error(e)
        return None

# API 키 설정
api_key = load_api_key_from_config()
if not api_key:
    # 프로그램 종료
    sys.exit(1)

# Gemini API 클라이언트 생성
client = genai.Client(api_key=api_key)

# 사용할 모델: Gemini 3.5 Flash Lite
GEMINI_MODEL = 'gemini-3.5-flash-lite'

# --- 2. 헬퍼 함수: Gemini API 호출 ---

# Gemini API 호출 시간/재시도 설정
# Cloud Run 에서 대기가 길어지면 비용이 늘고 작업이 끝나기 전에 인스턴스가 종료될 수 있으므로,
# call_gemini_api 한 번은 (API 응답 대기 + 재시도 대기를 모두 합쳐) 3분 안에 끝나도록 한다.
GEMINI_CALL_TIME_LIMIT = 170.0   # 호출 한 번에 쓸 수 있는 총 시간(초). 이 시간을 넘겨서 기다리지 않는다
GEMINI_MAX_ATTEMPTS = 4          # 최초 호출을 포함한 최대 시도 횟수
GEMINI_RETRY_BASE_DELAY = 10.0   # 서버가 대기 시간을 알려주지 않을 때의 첫 대기 시간(초). 이후 2배씩 증가 (10, 20, 40)
GEMINI_RETRY_MAX_DELAY = 60.0    # 한 번에 기다리는 최대 시간(초). 분당 한도가 풀리는 데 필요한 시간
GEMINI_RETRY_DELAY_MARGIN = 1.0  # 서버가 알려준 대기 시간에 더하는 여유(초)
GEMINI_MIN_ATTEMPT_TIME = 10.0   # 재시도한 호출이 응답을 받을 수 있도록 남겨두는 최소 시간(초)
GEMINI_COOLDOWN = 600.0          # 재시도로도 해결되지 않았을 때 API 호출을 건너뛰는 시간(초)

# 잠시 기다린 뒤 다시 시도할 오류의 HTTP 상태 코드: 429(할당량/요청 한도 초과), 503(서버 과부하)
GEMINI_RETRYABLE_STATUS_CODES = (429, 503)

# 서버가 시간 한도(요청에 실어 보내는 X-Server-Timeout) 안에 처리하지 못했을 때의 HTTP 상태 코드(DEADLINE_EXCEEDED)
GEMINI_SERVER_TIMEOUT_STATUS_CODE = 504

# google-genai 는 전송 계층 예외를 SDK 예외(errors.APIError)로 감싸지 않고 httpx2(기본) / httpx 의 예외 그대로 올린다.
# 아래 두 묶음은 TransportError 아래의 서로 다른 갈래라 겹치지 않는다.

# 클라이언트 쪽에서 응답 대기 시간(timeout)을 넘겼을 때의 예외 (ConnectTimeout, ReadTimeout, WriteTimeout, PoolTimeout)
GEMINI_TIMEOUT_ERRORS = tuple(module.TimeoutException for module in (httpx2, httpx) if module is not None)

# 연결이 실패하거나 끊겨 응답을 받지 못했을 때의 예외. 503 처럼 잠시 기다린 뒤 다시 시도한다.
# (구 SDK(gRPC)에서는 이런 오류가 UNAVAILABLE 로 올라와 503 과 같이 재시도됐다)
#   - NetworkError: ConnectError(연결 실패), ReadError / WriteError(주고받는 중 끊김), CloseError
#   - RemoteProtocolError: 서버가 응답 없이 연결을 닫는 등 서버 쪽에서 HTTP 규약이 깨진 경우
GEMINI_CONNECTION_ERRORS = tuple(
    error_cls
    for module in (httpx2, httpx) if module is not None
    for error_cls in (module.NetworkError, module.RemoteProtocolError)
)

# 429 응답 본문의 error.details 중 서버 권장 대기 시간을 담은 항목의 @type
_RETRY_INFO_TYPE = "type.googleapis.com/google.rpc.RetryInfo"

# API 호출에 실패했을 때 결과 대신 반환하는 문자열
API_ERROR_TEXT = "Error during API call."

# 이 시각(time.monotonic 기준) 전까지는 API 를 호출하지 않고 바로 실패 처리한다.
_gemini_blocked_until = 0.0

def _get_server_retry_delay(exc):
    """
    429 응답 본문(JSON)에 포함된 서버 권장 대기 시간(google.rpc.RetryInfo 의 retryDelay)을 초 단위로 반환합니다.
    정보가 없거나 형식이 예상과 다르면 None 을 반환합니다.

    errors.APIError 의 details 는 응답 본문 전체이며, 보통 아래 모양입니다.
      {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "...",
                 "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "28s"}, ...]}}
    """
    body = getattr(exc, "details", None)
    if not isinstance(body, dict):
        return None
    error = body.get("error", body)
    error_details = error.get("details") if isinstance(error, dict) else None
    if not isinstance(error_details, list):
        return None

    for detail in error_details:
        if not isinstance(detail, dict) or detail.get("@type") != _RETRY_INFO_TYPE:
            continue
        # retryDelay 는 "28s", "0.500s" 처럼 초 단위 숫자 뒤에 s 가 붙은 문자열이다 (protobuf Duration 의 JSON 표현)
        retry_delay = detail.get("retryDelay")
        if not isinstance(retry_delay, str) or not retry_delay.endswith("s"):
            continue
        try:
            delay = float(retry_delay[:-1])
        except ValueError:
            continue
        if math.isfinite(delay) and delay > 0:
            return delay
    return None

def _remaining_timeout_ms(deadline: float) -> int:
    """
    호출당 시간 한도(deadline)까지 남은 시간을 SDK 의 응답 대기 시간 단위(밀리초 정수)로 반환합니다.
    내림하므로 응답 대기가 시간 한도를 넘지 않습니다. 남은 시간이 1밀리초 미만이면 0 을 반환합니다.
    """
    return max(int((deadline - time.monotonic()) * 1000), 0)

def _gemini_request_config(timeout_ms: int):
    """
    generate_content 한 번에 쓸 요청 설정을 만듭니다.
      - timeout: 응답 대기 시간(밀리초). SDK 는 이 값이 0 이나 None 이면 시간 제한 없이 기다리므로 1 이상만 넘긴다.
      - retry_options(attempts=1): SDK 자체 재시도를 끈다. 요청별 설정이 클라이언트 설정보다 우선한다.
      - automatic_continuation=False: 응답이 CONTINUATION 으로 끝나도 SDK 가 후속 요청을 보내지 않게 한다.
        (후속 요청마다 timeout 이 새로 적용되어 호출당 시간 한도를 넘길 수 있으므로)
    """
    return types.GenerateContentConfig(
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
        automatic_continuation=False,
    )

def _describe_empty_response(response) -> str:
    """텍스트가 없는 응답의 원인(프롬프트 차단 사유, 첫 번째 후보의 종료 사유)을 로그용 문자열로 만듭니다."""
    prompt_feedback = getattr(response, "prompt_feedback", None)
    candidates = getattr(response, "candidates", None) or []
    block_reason = getattr(prompt_feedback, "block_reason", None)
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return f"block_reason={block_reason}, finish_reason={finish_reason}"

def _gemini_error_name(exc) -> str:
    """로그에서 오류 종류를 알아볼 이름: API 오류는 HTTP 상태 코드(예: 429), 전송 계층 오류는 예외 클래스 이름(예: ConnectError)"""
    if isinstance(exc, errors.APIError):
        return str(exc.code)
    return type(exc).__name__

def _describe_gemini_error(exc) -> str:
    """로그용 오류 설명: API 오류는 그대로(코드와 응답 본문 포함), 전송 계층 오류는 예외 클래스 이름을 앞에 붙인다."""
    if isinstance(exc, errors.APIError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"

def _plan_retry(exc, attempt: int, deadline: float):
    """
    재시도 여부와 대기 시간을 정합니다.
    :return: (대기 시간(초), None) 또는 재시도하지 않을 때 (None, 포기 사유)
    """
    if attempt >= GEMINI_MAX_ATTEMPTS:
        return None, f"{GEMINI_MAX_ATTEMPTS}회 시도 후에도 해결되지 않음"

    server_delay = _get_server_retry_delay(exc)
    if server_delay is None:
        delay = min(GEMINI_RETRY_BASE_DELAY * (2 ** (attempt - 1)), GEMINI_RETRY_MAX_DELAY)
    elif server_delay > GEMINI_RETRY_MAX_DELAY:
        return None, f"서버 권장 대기 시간({server_delay:.0f}초)이 너무 김"
    else:
        delay = server_delay + GEMINI_RETRY_DELAY_MARGIN

    if time.monotonic() + delay + GEMINI_MIN_ATTEMPT_TIME > deadline:
        return None, f"호출당 시간 한도({GEMINI_CALL_TIME_LIMIT:.0f}초) 안에 재시도할 수 없음"

    return delay, None

def _block_gemini_calls(reason: str):
    """Gemini API 를 당분간 쓸 수 없는 것으로 보고 GEMINI_COOLDOWN 동안 호출을 건너뛰도록 표시합니다."""
    global _gemini_blocked_until
    _gemini_blocked_until = time.monotonic() + GEMINI_COOLDOWN
    logger.error(f"{reason} -> 이후 {GEMINI_COOLDOWN:.0f}초 동안 Gemini API 호출을 건너뜁니다.")

def call_gemini_api(prompt_text):
    """
    주어진 프롬프트로 Gemini API를 호출하고 결과를 반환합니다. 실패하면 API_ERROR_TEXT 를 반환합니다.

    호출 한 번은 GEMINI_CALL_TIME_LIMIT(170초) 안에 끝납니다.
      - 429(할당량/요청 한도 초과), 503(서버 과부하), 연결 오류(연결 실패·끊김, GEMINI_CONNECTION_ERRORS)는
        기다렸다가 최대 GEMINI_MAX_ATTEMPTS 회까지 시도합니다.
        서버가 대기 시간(RetryInfo)을 알려주면 그 시간만큼, 아니면 10초부터 2배씩 늘려가며 기다립니다.
      - 시간 한도 안에 재시도할 수 없거나, 서버 권장 대기 시간이 GEMINI_RETRY_MAX_DELAY 보다 길거나
        (일일 할당량 소진 등), 모든 시도가 실패하면 더 기다리지 않고 포기합니다.
      - 포기한 뒤 GEMINI_COOLDOWN 동안의 호출은 API 를 부르지 않고 바로 실패 처리해,
        남은 게시물마다 대기 시간이 반복되지 않게 합니다.
      - 응답 대기 시간을 넘기면(클라이언트 쪽 시간 초과 또는 서버의 504) 재시도하지 않고 포기하며,
        위와 같이 GEMINI_COOLDOWN 동안의 호출을 건너뜁니다.
      - 응답에 텍스트가 없거나(안전 필터 차단 등) 그 밖의 오류는 재시도하지 않고 API_ERROR_TEXT 를 반환합니다.

    google-genai SDK 는 응답 대기 시간(timeout)을 지정하지 않으면 시간 제한 없이 기다리고, 재시도 설정
    (HttpRetryOptions)이 있으면 SDK 안에서 재시도합니다. 그래서 요청마다 남은 시간을 응답 대기 시간(밀리초)으로
    넘기고 SDK 자체 재시도는 꺼서(_gemini_request_config), 대기와 재시도를 모두 이 함수 안에서만 관리합니다.
    """
    if time.monotonic() < _gemini_blocked_until:
        logger.warning("Gemini API 를 당분간 쓸 수 없는 상태라 호출을 건너뜁니다.")
        return API_ERROR_TEXT

    deadline = time.monotonic() + GEMINI_CALL_TIME_LIMIT

    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        timeout_ms = _remaining_timeout_ms(deadline)
        if timeout_ms <= 0:
            # SDK 는 응답 대기 시간이 0 이면 시간 제한 없이 기다리므로, 남은 시간이 없으면 호출하지 않는다.
            _block_gemini_calls(f"Gemini API 호출당 시간 한도({GEMINI_CALL_TIME_LIMIT:.0f}초)를 모두 썼습니다")
            return API_ERROR_TEXT

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_text,
                config=_gemini_request_config(timeout_ms),
            )
            text = response.text
            if text is None:
                # 안전 필터 차단 등으로 응답에 텍스트가 없음 (구 SDK 는 이때 response.text 에서 ValueError 를 냈다)
                logger.error(f"Gemini API 응답에 텍스트가 없습니다: {_describe_empty_response(response)}")
                return API_ERROR_TEXT
            return text
        except errors.APIError as e:
            if e.code == GEMINI_SERVER_TIMEOUT_STATUS_CODE:
                _block_gemini_calls(f"Gemini API 가 시간 한도 안에 응답하지 않았습니다: {e}")
                return API_ERROR_TEXT
            if e.code not in GEMINI_RETRYABLE_STATUS_CODES:
                logger.error(f"Gemini API 호출 중 예기치 않은 오류 발생: {e}", exc_info=True)
                return API_ERROR_TEXT
            retry_error = e
        except GEMINI_TIMEOUT_ERRORS as e:
            _block_gemini_calls(f"Gemini API 가 시간 한도 안에 응답하지 않았습니다: {e}")
            return API_ERROR_TEXT
        except GEMINI_CONNECTION_ERRORS as e:
            retry_error = e
        except Exception as e:
            logger.error(f"Gemini API 호출 중 예기치 않은 오류 발생: {e}", exc_info=True)
            return API_ERROR_TEXT

        # 여기까지 오면 다시 시도할 오류(429, 503, 연결 오류)다.
        delay, give_up_reason = _plan_retry(retry_error, attempt, deadline)
        if give_up_reason:
            _block_gemini_calls(f"Gemini API 오류로 포기합니다 ({give_up_reason}): {_describe_gemini_error(retry_error)}")
            return API_ERROR_TEXT

        logger.warning(f"Gemini API {_gemini_error_name(retry_error)}. {delay:.1f}초 대기 후 재시도합니다. "
                       f"(시도 {attempt}/{GEMINI_MAX_ATTEMPTS})")
        time.sleep(delay)

def remove_duplicate_posts(posts_list: list) -> list:
    """
    주어진 게시물 리스트에서 'url'을 기준으로 중복된 게시물을 제거합니다.
    가장 최근에 추가된(또는 리스트의 앞쪽에 있는) 항목을 유지합니다.
    """
    seen_urls = set()
    unique_posts = []

    for item in posts_list:
        if 'url' in item and item['url'] not in seen_urls:
            unique_posts.append(item)
            seen_urls.add(item['url'])
        elif 'url' not in item: # URL이 없는 항목은 중복 검사 없이 추가
            unique_posts.append(item)
    return unique_posts

def process_posts(input_filename: str, summarized_posts: list):
    """
    JSON 파일을 읽고, 각 게시물을 처리한 후, processed_posts 리스트에 추가
    """
    try:
        logger.info(f"load data from {input_filename} ...")
        with open(input_filename, 'r', encoding='utf-8') as f:
            posts = json.load(f).get('data', [])
    except FileNotFoundError:
        logger.warning(f"입력 파일 '{input_filename}'을(를) 찾을 수 없습니다.")
        return
    except json.JSONDecodeError:
        logger.warning(f"'{input_filename}' 파일이 올바른 JSON 형식이 아닙니다.", exc_info=True)
        return    

    for i, post in enumerate(posts):
        original_text = post.get("text", "")
        post_identifier = post.get('id', f"index_{i}")
        logger.info(f"Tweet 처리 시작: {post_identifier}")

        if not original_text:
            logger.info("  - 내용이 비어있어 API 호출을 건너뜁니다.")
            summarized_posts.append(post)
            continue
        
        text_len = len(original_text)

        if text_len >= 250:
            logger.info(f"  - 내용 길이({text_len}) >= 250. 번역 및 타이틀 & 요약을 생성 진행합니다.")
            translation_prompt = f"Translate the following English text to Korean:\n\n---\n{original_text}\n---"
            post['translated_text'] = call_gemini_api(translation_prompt)
                        
            title_prompt = (f"Create a concise and representative title in Korean for the following English text. "
                            f"Provide only the title text without any quotation marks or extra words.\n\n---\n{original_text}\n---")
            post['title'] = call_gemini_api(title_prompt)

            summary_prompt = (
                f"Summarize the following English text into a 3-point bullet list in most natural and modern Korean. "
                f"Do not provide any other explanations or the original English text.:\n\n"
                f"---\n{original_text}\n---")
            post['summary'] = call_gemini_api(summary_prompt)
            
        elif 15 <= text_len < 250:
            logger.info(f"  - 내용 길이({text_len}) < 250. 번역만 진행합니다.")
            translation_prompt = (
                f"Translate the following English text into a single, most natural and modern Korean sentence. "
                f"Do not provide any other options or labels.:\n\n---\n{original_text}\n---")
            post['translated_text'] = call_gemini_api(translation_prompt)
            
        else:
            logger.info(f"  - 내용 길이({text_len}) < 15. 처리를 건너뜁니다.")

        summarized_posts.append(post)

# --- 3. 메인 로직 ---
def main(base_ymd: str, gcs_mode: bool = True, tweet_usernames: list = None):
    """
    번역&요약 스크립트 메인 실행 함수
    :param str base_ymd: post 수집 기준 일자 (yyyymmdd)
    :param bool gcs_mode: GCS 사용 여부
    :param list tweet_usernames: 변역&요약 할 tweet 유저이름 list (default: None)
    """
    logger.info("="*50)
    logger.info("Tweet 번역&요약 스크립트를 시작합니다.")
    
    summarized_posts = []

    if tweet_usernames is None:
        tweet_source_list = tweet_scrapper_post.TARGET_USERNAMES
    else:
        tweet_source_list = tweet_usernames

    # tweet_source_list = ['tweet_agg_one']
    
    try:
        for tweet_user in tweet_source_list:
            input_filename = os.path.join(pjt_home_path, 'data', f'{tweet_user}_posts.json')
        
            process_posts(input_filename, summarized_posts)
        
        output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts.json')

        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(summarized_posts, f, ensure_ascii=False, indent=4)

        # json 파일 GCS 에 업로드
        if gcs_mode:
            gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_filename,
                                                     date_str=base_ymd)

        logger.info(f"✅ 신규 Tweet 처리가 완료되었습니다. 결과가 '{output_filename}' 파일에 저장되었습니다.")
        
        # 임시 테스트 코드
        # with open(output_filename, 'r', encoding='utf-8') as f:
        #     summarized_posts = json.load(f)
        

        output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts_agg.json')

        if gcs_mode:
            ret = gcs_download_json.download_gcs_to_local(file_name='summarized_posts_agg.json', date_str=base_ymd)
            logger.info(f"download_gcs_to_local ret => {ret}")

            if ret == 0:
                with open(output_filename, 'r', encoding='utf-8') as f:
                    summarized_old_posts_agg = json.load(f)
                    logger.info(f"summarized_posts (old-agg) cnt => {len(summarized_old_posts_agg)}")

                logger.info(f"summarized_posts cnt => {len(summarized_posts)}")
                summarized_posts_agg = summarized_posts + summarized_old_posts_agg
                logger.info(f"summarized_posts (agg) cnt => {len(summarized_posts_agg)}")

                # 중복 제거 함수 호출
                summarized_posts_agg = remove_duplicate_posts(summarized_posts_agg)
                logger.info(f"summarized_posts (agg-non-dup) cnt => {len(summarized_posts_agg)}")
            else:
                summarized_posts_agg = summarized_posts

            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(summarized_posts_agg, f, ensure_ascii=False, indent=4)

            # json 파일 GCS 에 업로드
            gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_filename,
                                                     date_str=base_ymd)

            logger.info(f"✅ 통합 처리가 완료되었습니다. 결과가 '{output_filename}' 파일에 저장되었습니다.")
            logger.info("="*50)
            
            # (base_ymd - 1)/summarized_posts_agg.json 파일 다운로드
            base_ymd_prev = (dt.datetime.strptime(base_ymd, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d")
            ret = gcs_download_json.download_gcs_to_local(file_name='summarized_posts_agg.json', date_str=base_ymd_prev)
            logger.info(f"download_gcs_to_local ret {base_ymd_prev}/summarized_posts_agg.json for => {ret}")
            
            # (base_ymd - 1)/summarized_posts_agg.json 파일 과 (base_ymd)/summarized_posts_agg.json (업데이트하기 전) 파일 합체
            summarized_prev_posts_agg = []
            if ret == 0:
                with open(output_filename, 'r', encoding='utf-8') as f:
                    summarized_prev_posts_agg = json.load(f)
                    logger.info("loaded summarized_prev_posts_agg.json!!")
                    logger.info(f"summarized_prev_posts_agg cnt => {len(summarized_prev_posts_agg)}")
            else:
                # 전일자 파일을 받지 못한 경우 로컬 파일은 방금 저장한 당일자 agg 이므로 읽으면 안 된다.
                # (읽으면 당일 게시물 전체가 전일자 게시물로 취급되어 모두 [중복] 태그가 붙음)
                logger.warning(f"{base_ymd_prev}/summarized_posts_agg.json 이 없어 전일자 중복 체크를 건너뜁니다.")
                
            summarized_union_posts_agg = summarized_prev_posts_agg # + summarized_old_posts_agg  # 우선 prev_post_agg 파일만 적용
            
            union_urls = {post.get('url') for post in summarized_union_posts_agg if post.get('url')}
            logger.info(f"union_urls cnt => {len(union_urls)}")
                
            # 합체한 agg.json의 url과 비교하여 summarized_posts의 중복 url에 태그 처리
            dup_url_cnt = 0
            for post in summarized_posts:
                if post.get('url') in union_urls:
                    dup_url_cnt += 1
                    if post.get('title'):
                        post['title'] = f"[중복] {post['title']}"                        
                    elif post.get('translated_text'):
                        post['translated_text'] = f"[중복] {post['translated_text']}"
                        
            logger.info(f"summarized_posts dup_url_cnt => {dup_url_cnt}")
            
            # 중복 url에 태그 처리된 summarized_posts 저장 및 업로드
            output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts.json')

            with open(output_filename, 'w', encoding='utf-8') as f:
                json.dump(summarized_posts, f, ensure_ascii=False, indent=4)
                
            gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_filename,
                                                     date_str=base_ymd)

            logger.info(f"✅ 중복 처리가 완료되었습니다. 결과가 '{output_filename}' 파일에 저장되었습니다.")
            logger.info("="*50)
            
        
    except Exception:
        msg = traceback.format_exc()
        logger.error(msg)
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    
    # base_ymd 인자 추가
    parser.add_argument(
        "base_ymd",
        type=str,
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"),  # 기본값은 현재 날짜 (UTC 기준)
        help="post 수집 기준 일자 (yyyymmdd), 미입력 시 현재 날짜(UTC)가 기본값",
        nargs='?'
    )
    
    args = parser.parse_args()
    
    # base_ymd 유효성 검증
    try:
        dt.datetime.strptime(args.base_ymd, "%Y%m%d")
    except ValueError:
        parser.error(f"잘못된 날짜 형식입니다: {args.base_ymd}. yyyymmdd 형식으로 입력해주세요.")
    
    main(base_ymd=args.base_ymd, gcs_mode=True)
