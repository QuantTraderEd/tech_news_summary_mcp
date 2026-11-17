import os
import sys
import site
import logging
import traceback
import json
import math
import time
import datetime as dt
from collections import Counter

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

def build_reuse_posts(today_posts_agg: list, prev_posts_agg: list) -> dict:
    """
    당일·전일 누적 게시물로 이미 요약한 게시물의 재사용 맵(url -> 게시물)을 만듭니다.
    같은 url 이 양쪽에 있으면 당일 것이, 한 목록 안에 여러 번 있으면 앞쪽 것이 우선입니다.
    url 이 없는 항목은 넣지 않습니다.
    """
    reuse_posts = {}
    for posts_agg in (today_posts_agg, prev_posts_agg):
        for post in posts_agg:
            post_url = post.get('url')
            if post_url and post_url not in reuse_posts:
                reuse_posts[post_url] = post
    return reuse_posts

def _get_reusable_value(reuse_candidate, field: str):
    """
    재사용 후보 게시물에서 field 값을 꺼냅니다.
    값이 비어 있지 않은 문자열이고 API 실패 문구(API_ERROR_TEXT)가 아닐 때만 그 값을, 아니면 None 을 반환합니다.
    """
    if not reuse_candidate:
        return None
    value = reuse_candidate.get(field)
    if isinstance(value, str) and value.strip() and value != API_ERROR_TEXT:
        return value
    return None

def process_posts(input_filename: str, summarized_posts: list, reuse_posts: dict = None, stats: Counter = None):
    """
    JSON 파일을 읽고, 각 게시물을 처리한 후, summarized_posts 리스트에 추가

    이미 요약한 게시물은 Gemini API 를 다시 호출하지 않고 기존 결과를 재사용합니다.
      - reuse_posts 에서 url 로 찾은 게시물의 원문(text)이 지금 원문과 같을 때만 재사용 후보로 쓴다.
        원문이 다르면(수정된 글, 수집 실패 후 다시 수집된 글) 전부 다시 요약한다.
      - 재사용은 필드 단위다. 필요한 필드(translated_text / title / summary)마다 후보의 값이 비어 있지 않고
        API 실패 문구가 아니면 그 값을 쓰고, 아니면 그 필드만 Gemini API 를 호출한다.
      - 처리한 게시물은 reuse_posts 에 반영해, 같은 실행에서 같은 url 이 다시 나오면 재사용되게 한다.

    :param str input_filename: 수집 결과 파일 경로 ({user}_posts.json)
    :param list summarized_posts: 처리한 게시물을 추가할 리스트
    :param dict reuse_posts: 이미 요약한 게시물 맵 (url -> 게시물). 처리한 게시물이 이 맵에 반영된다.
                             None 이면 빈 맵으로 시작하므로 이전 누적에서 재사용하는 것은 없지만,
                             같은 호출 안에서 같은 url(같은 원문)이 다시 나오면 앞서 처리한 결과를 재사용한다
    :param Counter stats: 실행 통계를 누적할 Counter
                          (gemini_calls: Gemini API 호출 횟수, reused_posts: 필요한 필드를 모두 재사용한 게시물 수,
                           partially_reused_posts: 일부 필드만 재사용하고 나머지는 다시 요약한 게시물 수)
    """
    if reuse_posts is None:
        reuse_posts = {}
    if stats is None:
        stats = Counter()

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

        # 생성할 필드와 프롬프트 (Gemini API 는 이 순서대로 호출한다)
        field_prompts = []

        if text_len >= 250:
            logger.info(f"  - 내용 길이({text_len}) >= 250. 번역 및 타이틀 & 요약을 생성 진행합니다.")
            translation_prompt = f"Translate the following English text to Korean:\n\n---\n{original_text}\n---"
            field_prompts.append(('translated_text', translation_prompt))

            title_prompt = (f"Create a concise and representative title in Korean for the following English text. "
                            f"Provide only the title text without any quotation marks or extra words.\n\n---\n{original_text}\n---")
            field_prompts.append(('title', title_prompt))

            summary_prompt = (
                f"Summarize the following English text into a 3-point bullet list in most natural and modern Korean. "
                f"Do not provide any other explanations or the original English text.:\n\n"
                f"---\n{original_text}\n---")
            field_prompts.append(('summary', summary_prompt))

        elif 15 <= text_len < 250:
            logger.info(f"  - 내용 길이({text_len}) < 250. 번역만 진행합니다.")
            translation_prompt = (
                f"Translate the following English text into a single, most natural and modern Korean sentence. "
                f"Do not provide any other options or labels.:\n\n---\n{original_text}\n---")
            field_prompts.append(('translated_text', translation_prompt))

        else:
            logger.info(f"  - 내용 길이({text_len}) < 15. 처리를 건너뜁니다.")

        # 같은 url 로 이미 요약한 게시물이 있고 원문이 같으면 재사용 후보로 쓴다.
        post_url = post.get('url')
        reuse_candidate = reuse_posts.get(post_url) if post_url else None
        if reuse_candidate is not None and reuse_candidate.get('text') != original_text:
            reuse_candidate = None

        reused_fields = []
        for field, prompt_text in field_prompts:
            reusable_value = _get_reusable_value(reuse_candidate, field)
            if reusable_value is not None:
                post[field] = reusable_value
                reused_fields.append(field)
            else:
                stats['gemini_calls'] += 1
                post[field] = call_gemini_api(prompt_text)

        if reused_fields:
            if len(reused_fields) == len(field_prompts):
                stats['reused_posts'] += 1
                logger.info("  - 이미 요약한 게시물이라 기존 결과를 재사용합니다.")
            else:
                stats['partially_reused_posts'] += 1
                resummarized_fields = [field for field, _ in field_prompts if field not in reused_fields]
                logger.info(f"  - 기존 결과 일부를 재사용합니다. 재사용: {', '.join(reused_fields)}, "
                            f"다시 요약: {', '.join(resummarized_fields)}")

        summarized_posts.append(post)

        # 방금 처리한 게시물을 재사용 맵에 반영 (같은 실행에서 같은 url 이 다시 나오면 재사용)
        if post_url:
            reuse_posts[post_url] = post

def load_summarized_posts_agg(date_str: str):
    """
    GCS {date_str}/summarized_posts_agg.json 을 로컬 data 디렉토리에 내려받아 읽습니다.

    당일·전일 누적 파일은 로컬의 같은 경로(data/summarized_posts_agg.json)에 내려받으므로, 받은 직후에 읽어
    메모리에 올려 두고 그 내용을 씁니다.
    다운로드 실패, JSON 파싱 오류, 형식 오류(게시물(dict) 목록이 아님)는 예외로 올립니다.
    (깨진 누적 파일을 빈 것으로 보고 덮어쓰지 않도록)

    :param str date_str: 누적 파일 일자 (yyyymmdd)
    :return: (download_gcs_to_local 반환값, 게시물 list). 반환값이 0 이 아니면(파일 없음 등) 빈 list
    """
    ret = gcs_download_json.download_gcs_to_local(file_name='summarized_posts_agg.json', date_str=date_str)
    if ret != 0:
        return ret, []

    agg_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts_agg.json')
    with open(agg_filename, 'r', encoding='utf-8') as f:
        posts_agg = json.load(f)

    if not isinstance(posts_agg, list) or not all(isinstance(post, dict) for post in posts_agg):
        raise ValueError(f"{date_str}/summarized_posts_agg.json 이 게시물(dict) 목록 형식이 아닙니다.")

    return ret, posts_agg

def save_summarized_posts_agg(summarized_posts: list, summarized_old_posts_agg: list, base_ymd: str) -> list:
    """
    이번 실행에서 처리한 게시물과 시작할 때 받은 당일 누적 게시물을 합쳐 url 기준으로 중복을 제거한 뒤,
    로컬 summarized_posts_agg.json 에 쓰고 GCS {base_ymd}/ 에 업로드합니다.
    이번 실행에서 처리한 게시물이 앞에 오므로, 같은 url 이면 이번 실행의 결과가 남습니다.

    로컬 파일에는 전일 누적 파일이 내려받아져 있을 수 있으므로, 업로드하기 전에 항상 당일 누적 내용을 다시 씁니다.

    :param list summarized_posts: 지금까지 이번 실행에서 처리한 게시물 전체 (사용자 순서대로)
    :param list summarized_old_posts_agg: 시작할 때 GCS 에서 받은 당일 누적 게시물
    :param str base_ymd: 누적 파일 일자 (yyyymmdd)
    :return: 저장한 누적 게시물 list
    """
    output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts_agg.json')

    logger.info(f"summarized_posts cnt => {len(summarized_posts)}")
    summarized_posts_agg = summarized_posts + summarized_old_posts_agg
    logger.info(f"summarized_posts (agg) cnt => {len(summarized_posts_agg)}")

    # 중복 제거 함수 호출
    summarized_posts_agg = remove_duplicate_posts(summarized_posts_agg)
    logger.info(f"summarized_posts (agg-non-dup) cnt => {len(summarized_posts_agg)}")

    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(summarized_posts_agg, f, ensure_ascii=False, indent=4)

    # json 파일 GCS 에 업로드
    gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_filename,
                                             date_str=base_ymd)

    return summarized_posts_agg

# --- 3. 메인 로직 ---
def main(base_ymd: str, gcs_mode: bool = True, tweet_usernames: list = None):
    """
    번역&요약 스크립트 메인 실행 함수

    gcs_mode=True 이면 요약을 이어서 할 수 있게 다음과 같이 처리합니다.
      1. 요약을 시작하기 전에 당일(base_ymd)·전일(base_ymd - 1) 누적 파일(summarized_posts_agg.json)을 GCS 에서 받아,
         이미 요약한 게시물(같은 url, 같은 원문)은 Gemini API 를 다시 호출하지 않고 재사용한다. (같은 url 이면 당일 것이 우선)
         당일 누적 파일은 파싱·형식 오류이거나 다운로드 반환값이 0(성공)·1(파일 없음)이 아니면 요약을 시작하기 전에 중단하고,
         전일 누적 파일은 파싱·형식 오류이면 없는 것으로 보고 계속 진행한다.
      2. 사용자 한 명의 처리가 끝날 때마다 당일 누적 파일을 GCS 에 저장해, 중간에 인스턴스가 종료돼도
         그때까지의 요약이 남게 한다. (처리한 게시물이 없는 사용자는 건너뜀)
      3. 모든 사용자를 처리한 뒤 summarized_posts.json 에서 전일 누적 파일에 url 이 있는 게시물에 [중복] 태그를 붙인다.
    gcs_mode=False 이면 GCS 에 접근하지 않고 summarized_posts.json 만 로컬에 씁니다.

    :param str base_ymd: post 수집 기준 일자 (yyyymmdd)
    :param bool gcs_mode: GCS 사용 여부
    :param list tweet_usernames: 변역&요약 할 tweet 유저이름 list (default: None)
    """
    logger.info("="*50)
    logger.info("Tweet 번역&요약 스크립트를 시작합니다.")

    summarized_posts = []
    summary_stats = Counter()

    if tweet_usernames is None:
        tweet_source_list = tweet_scrapper_post.TARGET_USERNAMES
    else:
        tweet_source_list = tweet_usernames

    # tweet_source_list = ['tweet_agg_one']

    try:
        base_ymd_prev = (dt.datetime.strptime(base_ymd, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d")

        summarized_old_posts_agg = []   # 시작할 때 받은 당일 누적
        summarized_prev_posts_agg = []  # 전일 누적
        prev_agg_ret = None

        if gcs_mode:
            # 당일·전일 누적 파일은 로컬의 같은 경로에 내려받으므로 받은 직후에 읽어 메모리에 올려 둔다.
            # (base_ymd)/summarized_posts_agg.json 파일 다운로드
            # 당일 누적 파일의 파싱·형식 오류는 예외로 올려 중단한다. (깨진 파일을 빈 것으로 보고 덮어쓰면 안 됨)
            ret, summarized_old_posts_agg = load_summarized_posts_agg(base_ymd)
            logger.info(f"download_gcs_to_local ret => {ret}")
            if ret not in (0, 1):
                # 접근 오류(2) 등인데 빈 누적으로 시작하면 첫 사용자 처리 후 GCS 의 당일 누적을 이번 처리분만으로 덮어쓸 수 있다.
                raise RuntimeError(f"{base_ymd}/summarized_posts_agg.json 다운로드에 실패해(download_gcs_to_local ret => {ret}) "
                                   f"당일 누적을 덮어쓰지 않도록 중단합니다.")
            if ret == 0:
                logger.info(f"summarized_posts (old-agg) cnt => {len(summarized_old_posts_agg)}")

            # (base_ymd - 1)/summarized_posts_agg.json 파일 다운로드
            # 전일 누적 파일은 덮어쓰지 않으므로, 파싱·형식 오류(ValueError 계열)이면 없는 것으로 보고 계속 진행한다.
            # (다운로드 자체의 예외는 그대로 올라가 중단된다)
            try:
                prev_agg_ret, summarized_prev_posts_agg = load_summarized_posts_agg(base_ymd_prev)
            except ValueError as e:
                prev_agg_ret, summarized_prev_posts_agg = None, []
                logger.error(f"{base_ymd_prev}/summarized_posts_agg.json 을 읽지 못해(파싱·형식 오류) "
                             f"전일 누적이 없는 것으로 보고 계속 진행합니다. => {type(e).__name__}: {e}")
            else:
                logger.info(f"download_gcs_to_local ret {base_ymd_prev}/summarized_posts_agg.json for => {prev_agg_ret}")
                if prev_agg_ret == 0:
                    logger.info("loaded summarized_prev_posts_agg.json!!")
                    logger.info(f"summarized_prev_posts_agg cnt => {len(summarized_prev_posts_agg)}")

        # 이미 요약한 게시물 재사용 맵 (url -> 게시물, 같은 url 이면 당일 것이 우선)
        reuse_posts = build_reuse_posts(summarized_old_posts_agg, summarized_prev_posts_agg)
        logger.info(f"reuse_posts cnt => {len(reuse_posts)}")

        agg_saved = False
        for tweet_user in tweet_source_list:
            input_filename = os.path.join(pjt_home_path, 'data', f'{tweet_user}_posts.json')
            processed_cnt_before = len(summarized_posts)

            process_posts(input_filename, summarized_posts, reuse_posts, summary_stats)

            # 사용자 한 명의 처리가 끝날 때마다 당일 누적 파일을 저장한다. (처리한 게시물이 없으면 건너뜀)
            if gcs_mode and len(summarized_posts) > processed_cnt_before:
                save_summarized_posts_agg(summarized_posts, summarized_old_posts_agg, base_ymd)
                agg_saved = True

        logger.info(f"Tweet 요약 집계 - 처리 게시물: {len(summarized_posts)}건")
        logger.info(f"Tweet 요약 집계 - Gemini 호출: {summary_stats['gemini_calls']}회")
        logger.info(f"Tweet 요약 집계 - 재사용 게시물: {summary_stats['reused_posts']}건")
        logger.info(f"Tweet 요약 집계 - 일부 필드만 재사용: {summary_stats['partially_reused_posts']}건")

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
        

        if gcs_mode:
            # 당일 누적 파일은 사용자마다 이미 저장했다 (save_summarized_posts_agg)
            output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts_agg.json')
            if agg_saved:
                logger.info(f"✅ 통합 처리가 완료되었습니다. 결과가 '{output_filename}' 파일에 저장되었습니다.")
            else:
                logger.info("이번 실행에서 처리한 게시물이 없어 누적 파일을 저장하지 않았습니다.")
            logger.info("="*50)

            # 전일자 누적 파일(시작할 때 받아 둔 것)의 url 로 [중복] 태그를 붙인다.
            # 태그는 누적 파일 저장이 모두 끝난 뒤에 붙여야 누적 파일에 들어가지 않는다.
            if prev_agg_ret != 0:
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
