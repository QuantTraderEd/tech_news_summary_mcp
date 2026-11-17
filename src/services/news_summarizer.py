import os
import sys
import site
import logging
import traceback
import json
import datetime as dt

import pytz
from google import genai
from google.genai import errors, types

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)
from src.services import gcs_upload_json

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')

# config.json 파일에서 API 키 불러오기
with open(f'{pjt_home_path}/config.json', 'r') as config_file:
    config = json.load(config_file)
    api_key = config.get("GEMINI_API_KEY")
    
# Gemini API 키 설정
API_KEY = api_key

if not API_KEY:
    logger.error("Error: config.json 파일에 'GEMINI_API_KEY'가 없거나 값이 비어있습니다.")
    sys.exit(1)

# Gemini API 클라이언트 생성
client = genai.Client(api_key=API_KEY)

# 사용할 모델: Gemini 3.5 Flash Lite
GEMINI_MODEL = 'gemini-3.5-flash-lite'

# Gemini API 응답 대기/재시도 설정
# 구 SDK 는 기본으로 응답을 최대 600초 기다리고 503 을 600초 한도 안에서 재시도했지만,
# google-genai SDK 는 기본으로 응답 대기 시간 제한이 없고(무한 대기) 재시도도 하지 않으므로 직접 정한다.
#   - 응답 대기 시간은 시도마다 따로 적용된다.
#   - SDK 는 상태 코드와 상관없이 응답 대기 시간 초과(TimeoutException)와 연결 실패(ConnectError)도 같은 횟수
#     안에서 재시도한다. 응답을 주고받는 중 연결이 끊긴 경우(ReadError, RemoteProtocolError 등)는 재시도하지 않는다.
#   - 재시도 전 대기: 1초부터 1.3배씩 (최대 10초) + 0~1초 무작위 -> 3번 기다려도 최대 약 7초
#   - 기사 한 건의 최악 소요 시간: 120초 x 4회 + 약 7초 = 약 487초 (구 SDK 의 한도 600초 이내)
GEMINI_TIMEOUT_MS = 120_000   # 시도 한 번의 응답 대기 시간(밀리초). 0 이나 None 이면 시간 제한 없이 기다린다
GEMINI_MAX_ATTEMPTS = 4       # 최초 호출을 포함한 최대 시도 횟수

GEMINI_REQUEST_CONFIG = types.GenerateContentConfig(
    http_options=types.HttpOptions(
        timeout=GEMINI_TIMEOUT_MS,
        retry_options=types.HttpRetryOptions(
            attempts=GEMINI_MAX_ATTEMPTS,
            initial_delay=1.0,
            exp_base=1.3,
            max_delay=10.0,
            jitter=1.0,
            http_status_codes=[503],  # 상태 코드는 503(서버 과부하)만 재시도 (구 SDK 와 같은 대상)
        ),
    ),
    # 응답이 CONTINUATION 으로 끝나도 SDK 가 후속 요청을 보내지 않게 한다.
    # (후속 요청마다 위 대기/재시도가 새로 적용되어 최악 소요 시간을 넘길 수 있으므로)
    automatic_continuation=False,
)

# 요약 실패 문구에 넣는 Gemini API 오류 설명의 최대 길이(글자 수). 이 문구는 요약 JSON 과 메일 본문에 들어간다.
# Gemini 오류 메시지는 보통 한두 문장(100자 안팎)이고 여러 줄짜리 할당량 초과(429) 메시지도 400자 남짓이라 자르지 않고,
# 프록시/로드밸런서가 돌려주는 HTML 오류 페이지(수 KB) 같은 긴 본문만 잘라 낸다.
API_ERROR_DESCRIPTION_MAX_LENGTH = 500

def _describe_empty_response(response) -> str:
    """텍스트가 없는 응답의 원인(프롬프트 차단 사유, 첫 번째 후보의 종료 사유)을 로그용 문자열로 만듭니다."""
    prompt_feedback = getattr(response, "prompt_feedback", None)
    candidates = getattr(response, "candidates", None) or []
    block_reason = getattr(prompt_feedback, "block_reason", None)
    finish_reason = getattr(candidates[0], "finish_reason", None) if candidates else None
    return f"block_reason={block_reason}, finish_reason={finish_reason}"

def _describe_error(exc) -> str:
    """
    요약 실패 문구와 오류 로그에 쓸 오류 설명을 만듭니다.

    str(errors.APIError) 에는 응답 본문 전체가 들어가므로, Gemini API 오류는 구 SDK 와 같은
    "<코드> <설명>" 한 줄(예: "503 The model is overloaded.")로 만듭니다.
      - 설명은 message, message 가 비어 있으면 status(예: 본문 없는 504 의 "Gateway Timeout"), 둘 다 없으면 코드만 씁니다.
      - 연속된 공백·줄바꿈은 공백 하나로 줄이고, API_ERROR_DESCRIPTION_MAX_LENGTH 보다 길면 잘라서 끝에 "..." 를 붙입니다.
        (본문이 HTML 이면 SDK 가 본문 전체를 message 로 넣는다)
    그 밖의 오류는 str(exc) 를 그대로 씁니다.
    """
    if not isinstance(exc, errors.APIError):
        return str(exc)

    description = ""
    for text in (exc.message, exc.status):
        description = " ".join(str(text or "").split())
        if description:
            break

    if len(description) > API_ERROR_DESCRIPTION_MAX_LENGTH:
        description = description[:API_ERROR_DESCRIPTION_MAX_LENGTH].rstrip() + "..."

    return f"{exc.code} {description}" if description else f"{exc.code}"

def summarize_news(news_item, num_sentences=3):
    """
    단일 뉴스 아이템(딕셔너리)을 입력받아 지정된 문장 수로 요약합니다.
    """
    title = news_item.get('title', '제목 없음')
    content = news_item.get('content', '내용 없음')

    prompt = f"""
    다음 뉴스 기사를 {num_sentences}문장으로 간결하고 불렛 목록으로 요약해 주세요.
    불렛 목록은 줄바꿈 한번만 넣어서 작성해 주세요.
    핵심 내용을 중심으로 작성해 주세요.

    뉴스 제목: {title}
    뉴스 내용: {content}
    """

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=GEMINI_REQUEST_CONFIG,
        )
        # 응답에서 요약 텍스트 추출. 첫 번째 후보의 텍스트 파트를 이어 붙인 값입니다.
        text = response.text
        if text is None:
            # 안전 필터 차단 등으로 응답에 텍스트가 없음 (구 SDK 는 이때 response.text 에서 ValueError 를 냈다)
            raise ValueError(f"Gemini API 응답에 텍스트가 없습니다 ({_describe_empty_response(response)})")
        summary = text.strip()
        return summary
    except Exception as e:
        error_text = _describe_error(e)
        logger.error(f"뉴스 ID {news_item.get('id', 'N/A')} 요약 중 오류 발생: {error_text}")
        return f"요약 실패: {error_text}"

def main(base_ymd: str):
    
    news_source_list = ['zdnet_semiconductor',
                        'zdnet_computing',
                        'thelec_semiconductor',
                        'etnews_electronics',
                        'etnews_software',
                        'etnews_it']
    summarized_results = []
    
    try:
        for news_source in news_source_list:
            logger.info(f"뉴스사이트: {news_source}")
            json_file_path = f'{pjt_home_path}/data/{news_source}_articles.json' # 뉴스 데이터 JSON 파일 경로
            
            with open(json_file_path, 'r', encoding='utf-8') as f:
                news_data_list = json.load(f)        

            logger.info(f"총 {len(news_data_list)}개의 뉴스 기사를 요약합니다.\n")
            
            for news_item in news_data_list:
                news_title = news_item.get('title', 'N/A')
                news_url = news_item.get('url', 'N/A')
                logger.info(f"--- 뉴스 타이틀: {news_title} ---")
                summary = summarize_news(news_item, num_sentences=3)
                logger.info(f"요약:\n{summary}\n")

                summarized_results.append({
                    "title": news_title,
                    "date": news_item.get('published_date', 'N/A'),
                    "url":  news_url,     
                    "summary": summary
                })
         
        # 요약 결과를 'date'를 1차 기준으로, 'url'을 2차 기준으로 정렬
        sorted_results = sorted(summarized_results, key=lambda x: (x['date'], x['url']), reverse=True)
        
        # 요약된 결과를 새로운 JSON 파일로 저장
        output_json_path = f'{pjt_home_path}/data/summarized_news.json'
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(sorted_results, f, ensure_ascii=False, indent=2)
        logger.info(f"\n모든 요약이 완료되었습니다. 결과는 '{output_json_path}'에 저장되었습니다.")
        
        # json 파일 GCS 에 업로드
        gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_json_path,
                                                date_str=base_ymd)
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
        default=dt.datetime.now(kst_timezone).strftime("%Y%m%d"), # 기본값은 현재 날짜
        help="뉴스 기준 일자 (yyyymmdd), 미입력 시 현재 날짜가 기본값",
        nargs='?'
    )
    
    args = parser.parse_args()
    
    # base_ymd 유효성 검증
    try:
        dt.datetime.strptime(args.base_ymd, "%Y%m%d")
    except ValueError:
        parser.error(f"잘못된 날짜 형식입니다: {args.base_ymd}. yyyymmdd 형식으로 입력해주세요.")
    
    main(base_ymd=args.base_ymd)