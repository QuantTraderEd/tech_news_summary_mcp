import os
import sys
import site
import logging
import traceback
import re
import json
import datetime as dt
import pandas as pd  # pandas import 추가

import pytz

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s',
                              datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')


def clean_text(text):
    """
    트윗 텍스트에서 URL을 제거하는 함수입니다.
    """
    # URL 정규식 패턴
    url_pattern = re.compile(r'https?://\S+|www\.\S+')
    # 텍스트에서 URL 제거
    cleaned_text = url_pattern.sub(r'', text).strip()
    return cleaned_text


def process_tweets(json_file_path, csv_file_path, username=''):
    """
    JSON 파일을 읽고, 특정 키워드를 필터링하여 CSV 파일로 저장하는 메인 함수입니다.

    :param json_file_path: 입력 JSON 파일 경로
    :param csv_file_path: 출력 CSV 파일 경로
    :param username: 트위터 사용자 이름
    """
    # 필터링할 키워드 정의 (한글 -> 영문 매핑)
    korean_to_english_keywords = {
        '애플': 'apple',
        '엔비디아': 'nvidia',
        '아마존': 'amazon',
        '메타': 'meta',
        '테슬라': 'tesla',
        '마이크로소프트': 'microsoft',
        '구글': 'google',
        'AMD': 'AMD',
        '삼성': 'samsung',
        '하이닉스': 'hynix',
        '화웨이': 'huawei'
    }

    # 필터링할 Ticker 정의 (소문자로)
    tickers = [
        '$aapl', '$nvda', '$amzn' '$meta', '$tsla', '$msft', '$googl', '$tsm', '$amd'
    ]

    # 영문 키워드 리스트
    # 위 매핑에 없는 단어들은 직접 추가합니다.
    english_keywords = list(korean_to_english_keywords.values()) + [
        'tsmc', 'ai', 'datacenter', 'gpu', 'hbm', 'blackwell', 'b100', 'b200', 'hopper', 'h100', 'h200', 'h10', 'h20', 'ascend',
    ]
    # Ticker 리스트 추가
    english_keywords.extend(tickers)

    # 처리된 데이터를 저장할 리스트
    processed_data = []

    try:
        # JSON 파일 열기
        with open(json_file_path, 'r', encoding='utf-8') as f:
            tweets = json.load(f)
    except FileNotFoundError:
        logger.error(f"오류: '{json_file_path}' 파일을 찾을 수 없습니다.")
        return
    except json.JSONDecodeError:
        logger.error(f"오류: '{json_file_path}' 파일이 올바른 JSON 형식이 아닙니다.")
        return

    # 'data' 키가 있는지 확인
    if 'data' not in tweets:
        logger.error("오류: JSON 파일에 'data' 키가 없습니다.")
        return

    # 각 트윗을 순회하며 처리
    for tweet in tweets['data']:
        text = tweet.get('text', '').lower()  # 텍스트를 소문자로 변환하여 비교
        msg = tweet.get('text', '')[:50].replace('\n', '')

        logger.info(f"tweet: {tweet.get('created_at')} | Text: {msg} | id: {tweet.get('id')} ...")

        # 키워드가 포함되어 있는지 확인
        if any(keyword in text for keyword in english_keywords):
            logger.info("contained keyword !!")
            # created_at을 datetime 객체로 변환
            # ISO 8601 형식 문자열에서 'Z'를 제거하여 파싱
            created_at_str = tweet['created_at'].replace('Z', '')
            dt_object = dt.datetime.fromisoformat(created_at_str)
            # 타임존 처리 임시 코드
            dt_object = dt_object + dt.timedelta(hours=9)
            dt_object = kst_timezone.localize(dt_object)
            # 원하는 형식으로 datetime 문자열 포맷팅
            formatted_datetime = dt_object.strftime('%Y-%m-%d %H:%M:%S')

            # 트윗 ID
            tweet_id = tweet['id']

            # URL 생성
            url = f"https://x.com/{username}/status/{tweet_id}"

            # 텍스트에서 URL 제거
            cleaned_text_content = clean_text(tweet.get('text', ''))

            # 결과 저장
            processed_data.append({
                'username': username,
                'datetime': formatted_datetime,
                'text': cleaned_text_content,
                'url': url
            })

    # CSV 파일로 저장 (pandas 사용)
    if processed_data:
        # 데이터를 pandas DataFrame으로 변환
        df = pd.DataFrame(processed_data)

        # DataFrame을 CSV 파일로 저장 (인덱스 제외, UTF-8 with BOM 인코딩)
        df.to_csv(csv_file_path, sep="|", index=False, encoding='utf-8-sig')

        logger.info(f"성공: 필터링된 트윗 {len(processed_data)}개를 '{csv_file_path}' 파일에 저장했습니다.")
    else:
        logger.warning("결과: 지정된 키워드를 포함하는 트윗을 찾지 못했습니다.")


# --- 스크립트 실행 ---
# 입력 파일 이름: 'raywang_tweets.json'
# 출력 파일 이름: 'raywang_tweets_data.csv'
# 사용자 이름: 'raywang' (파일명을 기반으로 추정)
if __name__ == "__main__":
    # 이 스크립트와 동일한 디렉토리에 'raywang_tweets.json' 파일이 있어야 합니다.
    process_tweets(f'{pjt_home_path}/data/raywang_tweets.json',
                   f'{pjt_home_path}/data/raywang_tweets_data.csv',
                   username='raywang')
