import os
import sys
import logging
import traceback
import datetime as dt
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

def upload_json_files_to_gcs(local_data_dir=f'{pjt_home_path}/data', bucket_name='gcs-private-pjt-data', gcs_base_path='news_data', date_str=None):
    """
    JSON 파일을 로컬 디렉토리에서 Google Cloud Storage로 업로드합니다.

    Args:
        local_data_dir (str): JSON 파일이 있는 로컬 디렉토리 (예: 'data').
        bucket_name (str): GCS 버킷 이름.
        gcs_base_path (str): GCS 버킷 내의 기본 경로 (예: 'news_data').
        date_str (str, optional): GCS 경로에 사용할 날짜 문자열 (YYYYMMDD 형식).
                                  지정하지 않으면 현재 날짜를 사용합니다.
    """
    if not os.path.exists(local_data_dir):
        logger.error(f"로컬 데이터 디렉토리 '{local_data_dir}'가 존재하지 않습니다.")
        return

    storage_client = storage.Client()
    try:
        bucket = storage_client.bucket(bucket_name)
    except Exception as e:
        logger.error(f"버킷 '{bucket_name}'에 접근할 수 없습니다: {e}")
        return

    # 날짜 문자열이 제공되면 해당 문자열을 사용하고, 없으면 현재 날짜를 YYYYMMDD 형식으로 사용
    if date_str:
        # 날짜 문자열 형식 유효성 검사 (간단한 예시)
        if not (len(date_str) == 8 and date_str.isdigit()):
            logger.error(f"유효하지 않은 날짜 문자열 형식입니다: '{date_str}'. YYYYMMDD 형식이어야 합니다.")
            return
        upload_date = date_str
    else:
        upload_date = dt.datetime.now().strftime('%Y%m%d')

    gcs_destination_path = f"{gcs_base_path}/{upload_date}/"

    logger.info(f"JSON 파일을 '{local_data_dir}'에서 'gs://{bucket_name}/{gcs_destination_path}'(으)로 업로드 시작.")

    for filename in os.listdir(local_data_dir):
        if filename.endswith('.json'):
            local_file_path = os.path.join(local_data_dir, filename)
            blob_name = f"{gcs_destination_path}{filename}"
            blob = bucket.blob(blob_name)

            try:
                blob.upload_from_filename(local_file_path)
                logger.info(f"성공적으로 '{filename}'을(를) 'gs://{bucket_name}/{blob_name}'에 업로드했습니다.")
            except Exception as e:
                logger.error(f"'{filename}' 업로드 실패: {e}")
        else:
            logger.info(f"JSON 파일이 아닌 파일 건너뛰기: '{filename}'")

if __name__ == "__main__":
    # 테스트를 위해 'data' 디렉토리와 더미 JSON 파일 생성
    os.makedirs(f'{pjt_home_path}/data', exist_ok=True)
    with open(f'{pjt_home_path}/data/test_news_1.json', 'w') as f:
        f.write('{"title": "테스트 뉴스 1", "content": "이것은 테스트 기사입니다."}')
    with open(f'{pjt_home_path}/data/test_news_2.json', 'w') as f:
        f.write('{"title": "테스트 뉴스 2", "content": "또 다른 테스트 기사입니다."}')

    # 현재 날짜로 업로드
    logger.info("--- 현재 날짜로 업로드 시작 ---")
    upload_json_files_to_gcs()
    logger.info("--- 현재 날짜로 업로드 완료 ---")

    print("\n") # 구분선 추가

    # 특정 날짜로 업로드 (예: 2024년 1월 1일)
    logger.info("--- 특정 날짜 (20240101)로 업로드 시작 ---")
    upload_json_files_to_gcs(date_str='20240101')
    logger.info("--- 특정 날짜 (20240101)로 업로드 완료 ---")

    # 더미 파일 정리 (선택 사항)
    os.remove(f'{pjt_home_path}/data/test_news_1.json')
    os.remove(f'{pjt_home_path}/data/test_news_2.json')
    # os.rmdir('data')