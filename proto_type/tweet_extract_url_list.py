import os
import sys
import site
import traceback
import logging
import time
import json
import random
import datetime as dt

from bs4 import BeautifulSoup

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# --- 로거 설정 ---
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

# 스크립트와 동일한 디렉토리에 있는 HTML 파일의 경로
# 파일명이 다른 경우 이 부분을 수정해주세요.
file_name = f"{pjt_home_path}/tweet_following.html"

def extract_urls_from_html(file_path):
    """
    HTML 파일에서 모든 <a> 태그의 href 속성 값을 추출하여 반환합니다.

    Args:
        file_path (str): 읽어올 HTML 파일의 경로.

    Returns:
        list: 중복이 제거된 URL 리스트.
    """
    # 파일이 존재하는지 확인합니다.
    if not os.path.exists(file_path):
        logger.info(f"오류: '{file_path}' 파일을 찾을 수 없습니다. HTML 파일이 스크립트와 동일한 디렉토리에 있는지 확인하세요.")
        return []

    try:
        # 중복을 피하기 위해 set 자료형을 사용합니다.
        unique_urls = set()

        # UTF-8 인코딩으로 HTML 파일을 엽니다.
        with open(file_path, 'r', encoding='utf-8') as f:
            # BeautifulSoup 객체를 생성합니다.
            soup = BeautifulSoup(f, 'html.parser')

            # href 속성을 가진 모든 <a> 태그를 찾습니다.
            for a_tag in soup.find_all('a', href=True):
                # href 속성 값을 가져옵니다.
                url = a_tag['href']
                if url:  # URL이 비어있지 않은 경우에만 추가
                    unique_urls.add(url)

        # set을 list로 변환하여 정렬한 후 반환합니다.
        return sorted(list(unique_urls))

    except Exception as e:
        logger.error(f"파일을 처리하는 중 오류가 발생했습니다: {e}")
        return []

if __name__ == "__main__":
    # 함수를 호출하여 URL을 추출합니다.
    all_urls = extract_urls_from_html(file_name)

    if all_urls:
        print(f"총 {len(all_urls)}개의 고유한 URL을 찾았습니다.")

        # URL의 마지막 부분이 숫자인 경우만 필터링합니다.
        filtered_urls = []
        for url in all_urls:
            # URL 끝에 있을 수 있는 '/'를 제거하고 '/'로 분리한 후 마지막 부분을 가져옵니다.
            last_part = url.strip('/').split('/')[-1]
            if len(url.strip('/').split('/')) >= 2:
                last2_part = url.strip('/').split('/')[-2]
            else:
                continue
            if last_part.isdigit() and last2_part == 'status':
                filtered_urls.append("https://x.com" + url)

        print(f"\n필터링 후 {len(filtered_urls)}개의 URL이 남았습니다.\n")

        # 추출된 URL들을 화면에 출력합니다.
        for url in filtered_urls:
            logger.info(url)

            # 결과를 JSON 파일로 저장합니다.
            output_file_name = f"{pjt_home_path}/data/extracted_post_urls.json"
            with open(output_file_name, 'w', encoding='utf-8') as f:
                # json.dump를 사용하여 URL 리스트를 json 형식으로 저장합니다.
                # ensure_ascii=False는 한글이 깨지지 않도록 합니다.
                # indent=4는 가독성을 위해 들여쓰기를 추가합니다.
                json.dump(filtered_urls, f, ensure_ascii=False, indent=4)

        logger.info(f"\n추출된 URL 목록이 '{output_file_name}' 파일로 저장되었습니다.")
