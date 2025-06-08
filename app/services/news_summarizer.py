import os
import sys
import logging
import traceback
import json

import google.generativeai as genai

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

from app.services import gcs_upload_json

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

# config.json 파일에서 API 키 불러오기
with open(f'{pjt_home_path}/config.json', 'r') as config_file:
    config = json.load(config_file)
    api_key = config.get("GEMINI_API_KEY")
    
# Gemini API 키 설정
API_KEY = api_key

if not API_KEY:
    logger.error("Error: GOOGLE_API_KEY 환경 변수가 설정되지 않았습니다.")
    logger.error("API 키를 직접 코드에 입력하거나, 환경 변수로 설정해주세요.")
    # 여기에 직접 API 키를 입력할 수도 있지만, 보안상 권장하지 않습니다.
    # API_KEY = "YOUR_API_KEY"
    exit()

genai.configure(api_key=API_KEY)

# Gemini 1.5 Flash 모델 로드 (혹은 'gemini-1.5-pro-latest')
# Gemini 1.5 Flash는 'gemini-1.5-flash-latest' 또는 'gemini-1.5-flash'를 사용합니다.
model = genai.GenerativeModel('gemini-1.5-flash')

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
        response = model.generate_content(prompt)
        # 응답에서 요약 텍스트 추출. 보통 첫 번째 파트의 텍스트입니다.
        summary = response.text.strip()
        return summary
    except Exception as e:
        logger.error(f"뉴스 ID {news_item.get('id', 'N/A')} 요약 중 오류 발생: {e}")
        return f"요약 실패: {e}"

def main(base_ymd: str):
    
    news_source_list = ['zdnet', 'thelec']
    summarized_results = []
    
    try:
        for news_source in news_source_list:
            logger.info(f"뉴스사이트: {news_source}")
            json_file_path = f'{pjt_home_path}/data/{news_source}_semiconductor_articles.json' # 뉴스 데이터 JSON 파일 경로
            
            with open(json_file_path, 'r', encoding='utf-8') as f:
                news_data_list = json.load(f)        

            logger.info(f"총 {len(news_data_list)}개의 뉴스 기사를 요약합니다.\n")
            
            for news_item in news_data_list:
                news_title = news_item.get('title', 'N/A')
                logger.info(f"--- 뉴스 타이틀: {news_title} ---")
                summary = summarize_news(news_item, num_sentences=3)
                logger.info(f"요약:\n{summary}\n")

                summarized_results.append({
                    "title": news_title,
                    "date": news_item.get('published_date', 'N/A'),            
                    "summary": summary
                })

        # 요약된 결과를 새로운 JSON 파일로 저장
        output_json_path = f'{pjt_home_path}/data/summarized_news.json'
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(summarized_results, f, ensure_ascii=False, indent=2)
        logger.info(f"\n모든 요약이 완료되었습니다. 결과는 '{output_json_path}'에 저장되었습니다.")
        
        # json 파일 GCS 에 업로드
        gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_json_path,
                                                date_str=base_ymd)
    except Exception as e:
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