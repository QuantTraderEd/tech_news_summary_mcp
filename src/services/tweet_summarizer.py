import os
import sys
import site
import logging
import traceback
import json
import time
import datetime as dt

import pytz
import google.generativeai as genai

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


# 사용할 Gemini 모델 설정 ('gemini-pro', 'gemini-1.5-flash')
# 'gemini-1.5-flash'가 더 최신이고 빠르며 비용 효율적일 수 있습니다.
model = genai.GenerativeModel('gemini-1.5-flash')

# --- 2. 헬퍼 함수: Gemini API 호출 ---

def call_gemini_api(prompt_text):
    """
    주어진 프롬프트로 Gemini API를 호출하고 결과를 반환합니다.
    API 호출 제한(분당 요청 수)을 피하기 위해 재시도 로직을 포함합니다.
    """
    try:
        response = model.generate_content(prompt_text)
        return response.text
    except Exception as e:
        if "rate limit" in str(e).lower():
            logger.warning("API Rate limit exceeded. Waiting for 20 seconds before retrying...")
            time.sleep(20)
            return call_gemini_api(prompt_text)
        else:
            logger.error(f"Gemini API 호출 중 예기치 않은 오류 발생: {e}", exc_info=True)
            return "Error during API call."


# --- 3. 메인 로직 ---

def process_posts(input_filename: str, summarized_posts: list):
    """
    JSON 파일을 읽고, 각 게시물을 처리한 후, processed_posts 리스트에 추가
    """
    try:
        logger.info(f"load data from {input_filename} ...")
        with open(input_filename, 'r', encoding='utf-8') as f:
            posts = json.load(f).get('data', [])
    except FileNotFoundError:
        logger.warning(f"입력 파일 '{input_filename}'을(를) 찾을 수 없습니다.", exc_info=True)
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
                        
            title_prompt = f"Create a concise and representative title in Korean for the following English text. Provide only the title text without any quotation marks or extra words.\n\n---\n{original_text}\n---"
            post['title'] = call_gemini_api(title_prompt)
            
            summary_prompt = f"Summarize the following English text into a 3-point bullet list in Korean:\n\n---\n{original_text}\n---"
            post['summary'] = call_gemini_api(summary_prompt)
            
        elif 15 <= text_len < 250:
            logger.info(f"  - 내용 길이({text_len}) < 250. 번역만 진행합니다.")
            translation_prompt = f"Translate the following English text to Korean:\n\n---\n{original_text}\n---"
            post['translated_text'] = call_gemini_api(translation_prompt)
            
        else:
            logger.info(f"  - 내용 길이({text_len}) < 15. 처리를 건너뜁니다.")

        summarized_posts.append(post)

def main(base_ymd: str):
    """
    메인 실행 함수
    """
    logger.info("="*50)
    logger.info("Tweet 번역&요약 스크립트를 시작합니다.")
    
    summarized_posts = []
    
    tweet_source_list = [
        'rwang07',
        'MooreMorrisSemi',
        'The_AI_Investor',
        'wallstengine',
    ]
    
    try:
        for tweet_user in tweet_source_list:
            input_filename = os.path.join(pjt_home_path, 'data', f'{tweet_user}_posts.json')
        
            process_posts(input_filename, summarized_posts)
        
        output_data = {'data': summarized_posts}
        
        output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts.json')

        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(summarized_posts, f, ensure_ascii=False, indent=4)

        logger.info(f"✅ 처리가 완료되었습니다. 결과가 '{output_filename}' 파일에 저장되었습니다.")
        logger.info("="*50)
        
        # json 파일 GCS 에 업로드
        gcs_upload_json.upload_local_file_to_gcs(local_file_path=output_filename,
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

