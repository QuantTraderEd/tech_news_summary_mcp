import os
import sys
import site
import logging
import traceback
import json
import time
import datetime as dt

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
from google.rpc import error_details_pb2

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
genai.configure(api_key=api_key)


# Gemini 3.5 Flash Lite 모델 로드
model = genai.GenerativeModel('gemini-3.5-flash-lite')

# --- 2. 헬퍼 함수: Gemini API 호출 ---

# 429(할당량/요청 한도 초과) 응답에 대한 재시도 설정
GEMINI_MAX_ATTEMPTS = 5         # 최초 호출을 포함한 최대 시도 횟수
GEMINI_RETRY_BASE_DELAY = 5.0   # 서버가 대기 시간을 알려주지 않을 때의 첫 대기 시간(초). 이후 2배씩 증가
GEMINI_RETRY_MAX_DELAY = 60.0   # 한 번에 기다리는 최대 시간(초)
GEMINI_RETRY_DELAY_MARGIN = 1.0 # 서버가 알려준 대기 시간에 더하는 여유(초)
GEMINI_QUOTA_COOLDOWN = 600.0   # 재시도로도 해결되지 않았을 때 API 호출을 건너뛰는 시간(초)

# API 호출에 실패했을 때 결과 대신 반환하는 문자열
API_ERROR_TEXT = "Error during API call."

# 이 시각(time.monotonic 기준) 전까지는 할당량 소진으로 보고 API 를 호출하지 않는다.
_quota_blocked_until = 0.0

def _get_server_retry_delay(exc):
    """
    429 응답에 포함된 서버 권장 대기 시간(google.rpc.RetryInfo)을 초 단위로 반환합니다.
    정보가 없으면 None 을 반환합니다.
    """
    for detail in getattr(exc, "details", None) or []:
        if isinstance(detail, error_details_pb2.RetryInfo):
            delay = detail.retry_delay.seconds + detail.retry_delay.nanos / 1e9
            if delay > 0:
                return delay
    return None

def _block_gemini_calls(reason: str):
    """할당량이 소진된 것으로 보고 GEMINI_QUOTA_COOLDOWN 동안 API 호출을 건너뛰도록 표시합니다."""
    global _quota_blocked_until
    _quota_blocked_until = time.monotonic() + GEMINI_QUOTA_COOLDOWN
    logger.error(f"{reason} -> 이후 {GEMINI_QUOTA_COOLDOWN:.0f}초 동안 Gemini API 호출을 건너뜁니다.")

def call_gemini_api(prompt_text):
    """
    주어진 프롬프트로 Gemini API를 호출하고 결과를 반환합니다.

    429(할당량/요청 한도 초과, ResourceExhausted) 응답은 잠시 기다리면 풀리는 경우가 많으므로
    최대 GEMINI_MAX_ATTEMPTS 회까지 시도합니다.
      - 서버가 대기 시간(RetryInfo)을 알려주면 그 시간만큼, 아니면 5초부터 2배씩 늘려가며 기다립니다.
      - 서버가 알려준 대기 시간이 GEMINI_RETRY_MAX_DELAY 보다 길거나, 모든 시도가 429 로 끝나면
        (일일 할당량 소진 등) 더 기다리지 않고 포기합니다. 이후 GEMINI_QUOTA_COOLDOWN 동안의 호출은
        API 를 부르지 않고 바로 실패 처리해, 남은 게시물마다 대기 시간이 반복되지 않게 합니다.

    실패하면 API_ERROR_TEXT 를 반환합니다.
    (503 은 SDK 가 기본으로 재시도하므로 여기서 따로 처리하지 않습니다.)
    """
    if time.monotonic() < _quota_blocked_until:
        logger.warning("Gemini API 할당량 소진 상태라 호출을 건너뜁니다.")
        return API_ERROR_TEXT

    for attempt in range(1, GEMINI_MAX_ATTEMPTS + 1):
        try:
            response = model.generate_content(prompt_text)
            return response.text
        except google_exceptions.TooManyRequests as e:
            if attempt == GEMINI_MAX_ATTEMPTS:
                _block_gemini_calls(f"Gemini API 429 오류가 {GEMINI_MAX_ATTEMPTS}회 시도 후에도 해결되지 않았습니다: {e}")
                return API_ERROR_TEXT

            server_delay = _get_server_retry_delay(e)
            if server_delay is None:
                delay = min(GEMINI_RETRY_BASE_DELAY * (2 ** (attempt - 1)), GEMINI_RETRY_MAX_DELAY)
            elif server_delay > GEMINI_RETRY_MAX_DELAY:
                _block_gemini_calls(f"Gemini API 429 오류: 서버 권장 대기 시간({server_delay:.0f}초)이 너무 깁니다: {e}")
                return API_ERROR_TEXT
            else:
                delay = server_delay + GEMINI_RETRY_DELAY_MARGIN

            logger.warning(f"Gemini API 429(할당량/요청 한도 초과). {delay:.1f}초 대기 후 재시도합니다. "
                           f"(시도 {attempt}/{GEMINI_MAX_ATTEMPTS})")
            time.sleep(delay)
        except Exception as e:
            logger.error(f"Gemini API 호출 중 예기치 않은 오류 발생: {e}", exc_info=True)
            return API_ERROR_TEXT

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
