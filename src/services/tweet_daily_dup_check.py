import os
import sys
import site
import logging
import traceback
import json
import datetime as dt

import pytz

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

from src.services import gcs_download_json

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')


def download_agg_json(date_str: str) -> list:
    """
    GCS에서 지정 일자의 summarized_posts_agg.json 파일을 다운로드하고 내용을 반환합니다.

    Args:
        date_str (str): 다운로드 대상 날짜 (YYYYMMDD 형식)

    Returns:
        list: 다운로드된 게시물 리스트. 실패 시 빈 리스트 반환.
    """
    local_file_path = os.path.join(pjt_home_path, 'data')
    local_file_full = os.path.join(local_file_path, 'summarized_posts_agg.json')

    ret = gcs_download_json.download_gcs_to_local(
        file_name='summarized_posts_agg.json',
        date_str=date_str
    )
    logger.info(f"download_gcs_to_local ret ({date_str}/summarized_posts_agg.json) => {ret}")

    if ret != 0:
        logger.warning(f"{date_str}/summarized_posts_agg.json 다운로드 실패 (ret={ret}). 빈 리스트를 반환합니다.")
        return []

    try:
        with open(local_file_full, 'r', encoding='utf-8') as f:
            posts = json.load(f)
            logger.info(f"{date_str}/summarized_posts_agg.json 로드 완료. 게시물 수: {len(posts)}")
            return posts
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"{date_str}/summarized_posts_agg.json 파일 읽기 실패: {e}")
        return []


def mark_duplicate_posts(today_posts: list, prev_posts: list) -> list:
    """
    전일자 게시물의 URL과 비교하여 당일 게시물의 중복 항목에 '[중복] ' 태그를 추가합니다.

    중복 판별 기준: 전일자 summarized_posts_agg.json 에 동일한 url이 존재하는 경우
    태그 처리 우선순위:
      1. 'title' 필드가 있으면 title 앞에 '[중복] ' 추가
      2. 'title'이 없고 'translated_text'가 있으면 translated_text 앞에 '[중복] ' 추가

    Args:
        today_posts (list): 당일 게시물 리스트
        prev_posts (list): 전일자 게시물 리스트

    Returns:
        list: 중복 태그가 처리된 당일 게시물 리스트
    """
    # 전일자 게시물의 URL 집합 생성
    prev_urls = {post.get('url') for post in prev_posts if post.get('url')}
    logger.info(f"전일자 게시물 URL 수: {len(prev_urls)}")

    dup_url_cnt = 0
    for post in today_posts:
        post_url = post.get('url')
        if post_url and post_url in prev_urls:
            dup_url_cnt += 1
            if post.get('title'):
                post['title'] = f"[중복] {post['title']}"
            elif post.get('translated_text'):
                post['translated_text'] = f"[중복] {post['translated_text']}"

    logger.info(f"중복 URL 건수: {dup_url_cnt} / 전체 당일 게시물 수: {len(today_posts)}")
    return today_posts


def save_posts_to_json(posts: list, output_path: str):
    """
    게시물 리스트를 'created_at' 기준 최신순(내림차순)으로 정렬하여 JSON 파일로 저장합니다.

    Args:
        posts (list): 저장할 게시물 리스트
        output_path (str): 저장 파일 경로
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # created_at 기준 내림차순 정렬 (최신 → 오래된 순서)
    # created_at이 없는 항목은 빈 문자열로 처리하여 맨 뒤로 배치
    sorted_posts = sorted(posts, key=lambda x: x.get('created_at', ''), reverse=True)
    logger.info(f"created_at 기준 내림차순 정렬 완료 (게시물 수: {len(sorted_posts)})")

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sorted_posts, f, ensure_ascii=False, indent=4)

    logger.info(f"파일 저장 완료: '{output_path}' (게시물 수: {len(sorted_posts)})")


# --- 메인 로직 ---
def main(base_ymd: str):
    """
    중복 체크 메인 실행 함수

    1. 입력 일자(base_ymd)의 summarized_posts_agg.json 다운로드
    2. 전일자(base_ymd - 1)의 summarized_posts_agg.json 다운로드
    3. 전일자 파일의 URL과 비교하여 당일 게시물 중복 항목에 '[중복] ' 태그 처리
    4. 결과를 ./data/summarized_posts.json 에 저장

    Args:
        base_ymd (str): 기준 일자 (YYYYMMDD 형식)
    """
    logger.info("=" * 50)
    logger.info("중복 체크 스크립트를 시작합니다.")
    logger.info(f"기준 일자: {base_ymd}")

    try:
        # 1. 입력 일자의 summarized_posts_agg.json 다운로드
        logger.info(f"[STEP 1] {base_ymd}/summarized_posts_agg.json 다운로드 시작...")
        today_posts = download_agg_json(base_ymd)

        if not today_posts:
            logger.error(f"{base_ymd}/summarized_posts_agg.json 데이터가 없습니다. 처리를 중단합니다.")
            sys.exit(1)

        # 2. 전일자 계산 및 summarized_posts_agg.json 다운로드
        base_ymd_prev = (dt.datetime.strptime(base_ymd, "%Y%m%d") - dt.timedelta(days=1)).strftime("%Y%m%d")
        logger.info(f"[STEP 2] 전일자 {base_ymd_prev}/summarized_posts_agg.json 다운로드 시작...")
        prev_posts = download_agg_json(base_ymd_prev)

        if not prev_posts:
            logger.warning(f"전일자 {base_ymd_prev}/summarized_posts_agg.json 데이터가 없습니다. 중복 체크 없이 원본을 저장합니다.")

        # 3. 중복 체크 및 태그 처리
        logger.info("[STEP 3] 중복 체크 및 태그 처리 시작...")
        marked_posts = mark_duplicate_posts(today_posts, prev_posts)

        # 4. 결과 저장
        output_filename = os.path.join(pjt_home_path, 'data', 'summarized_posts.json')
        logger.info(f"[STEP 4] 결과 저장: {output_filename}")
        save_posts_to_json(marked_posts, output_filename)

        logger.info(f"✅ 중복 체크가 완료되었습니다. 결과가 '{output_filename}' 파일에 저장되었습니다.")
        logger.info("=" * 50)

    except Exception as e:
        msg = traceback.format_exc()
        logger.error(msg)
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='중복 URL 체크 스크립트: 당일/전일 summarized_posts_agg.json 비교')

    # base_ymd 인자 추가
    parser.add_argument(
        "base_ymd",
        type=str,
        default=dt.datetime.now(kst_timezone).strftime("%Y%m%d"),  # 기본값은 현재 날짜(KST)
        help="기준 일자 (YYYYMMDD 형식), 미입력 시 현재 날짜가 기본값",
        nargs='?'
    )

    args = parser.parse_args()

    # base_ymd 유효성 검증
    try:
        dt.datetime.strptime(args.base_ymd, "%Y%m%d")
    except ValueError:
        parser.error(f"잘못된 날짜 형식입니다: {args.base_ymd}. YYYYMMDD 형식으로 입력해주세요.")

    main(base_ymd=args.base_ymd)
