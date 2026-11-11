import os
import sys
import logging
import traceback
import datetime as dt

import pytz
from google.cloud import storage

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')

def upload_local_file_to_gcs(local_file_path: str, 
                             bucket_name: str ='gcs-private-pjt-data', 
                             gcs_base_path: str ='news_data', 
                             date_str: str ='20000101'):
    """
    로컬 파일을 Google Cloud Storage로 업로드합니다.

    Args:
        local_file_path (str): JSON 경로 + 파일이름 (예: '/data/news_data.json').
        bucket_name (str): GCS 버킷 이름.
        gcs_base_path (str): GCS 버킷 내의 기본 경로 (예: 'news_data').
        date_str (str, optional): GCS 경로에 사용할 날짜 문자열 (YYYYMMDD 형식).
                                  지정하지 않으면 과거 날짜 (20000101) 를 사용합니다.
    Returns:
        int: 0: 성공, 1: 로컬파일 없음, 2: gcs 접근 애러
    """
    if not os.path.exists(local_file_path):
        logger.error(f"local data file '{local_file_path}' doesn't exist!!")
        return 1

    storage_client = storage.Client()
    try:
        bucket = storage_client.bucket(bucket_name)
    except Exception as e:
        logger.error(f"bucket '{bucket_name}': can not access!! {e}")
        return 2

    gcs_destination_path = f"{gcs_base_path}/{date_str}/"

    logger.info(f"start upload '{local_file_path}' file to 'gs://{bucket_name}/{gcs_destination_path}'...")
        
    filename = os.path.basename(local_file_path)
    blob_name = f"{gcs_destination_path}{filename}"
    blob = bucket.blob(blob_name)

    try:
        blob.upload_from_filename(local_file_path)
        logger.info(f"finish to upload '{filename}' to 'gs://{bucket_name}/{blob_name}'!!!")
    except Exception as e:
        logger.error(f"'{filename}' upload fail!!! => {e}")
        raise
    
    return 0
            
def main(target_news_site: str, base_ymd: str):
    """
    articles.json 파일 GCS 업로드 메인 배치
    :param str target_news_site: 뉴스 수집 사이트 이름 (zdnet, thelec, etnews)
    :param str base_ymd: GCS 업로드 날짜 (yyyymmdd)  
    :return: None
    """
    
    local_data_dir=f'{pjt_home_path}/data'
    try:
        for filename in os.listdir(local_data_dir):
            if filename.endswith('.json') and filename.startswith(target_news_site):
                local_file_path = os.path.join(local_data_dir, filename)
                upload_local_file_to_gcs(local_file_path, date_str=base_ymd)
            else:
                logger.info(f"skip target file...: '{filename}'")
    except Exception:
        err_msg = traceback.format_exc()
        logger.error(err_msg)
        sys.exit(1)

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser('articles.json 파일 GCS 업로드')
    
    # target_news_site 인자 추가
    parser.add_argument(
        "target_news_site",
        type=str,        
        default="zdnet",
        choices=["zdnet", "thelec", "etnews"],
        help="뉴스 수집 사이트 [%(choices)s] default=[%(default)s]",
        metavar='target_news_site',
        nargs='?'
    )
    
    # base_ymd 인자 추가
    parser.add_argument(
        "base_ymd",
        type=str,
        default=dt.datetime.now(kst_timezone).strftime("%Y%m%d"), # 기본값은 현재 날짜
        help="뉴스 데이터 기준 일자 (yyyymmdd), 미입력 시 현재 날짜가 기본값",
        nargs='?'
    )
    
    args = parser.parse_args()

    # base_ymd 유효성 검증
    try:
        dt.datetime.strptime(args.base_ymd, "%Y%m%d")
    except ValueError:
        parser.error(f"잘못된 날짜 형식입니다: {args.base_ymd}. yyyymmdd 형식으로 입력해주세요.")

    main(target_news_site=args.target_news_site, base_ymd=args.base_ymd)
    