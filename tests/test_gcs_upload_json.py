import os
import sys
import site
import logging
import traceback
import pytest

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

from app.services import gcs_upload_json

# 로깅 설정
logger = logging.getLogger(__file__)

# 1. 단위테스트 시 필요한 패키지 추가 설치
# pip install pytest pytest-mock
# 2. 단위테스트 실행 커멘드
# pytest -vs tests/test_gcs_upload_json.py
# -v 옵션: 상세한 테스트 결과를 보여줍니다.
# -s 옵션: logging 출력을 캡처하지 않고 표준 출력을 표시합니다 (로그 메시지를 직접 볼 수 있습니다).


@pytest.fixture
def mock_gcs(mocker):
    """
    Google Cloud Storage 클라이언트, 버킷, 블롭 객체를 목(mock) 처리하는 픽스처.
    """
    # Blob 객체의 upload_from_filename 메서드를 목(mock) 처리합니다.
    mock_blob = mocker.MagicMock()
    mock_blob.upload_from_filename.return_value = None # 성공적인 업로드를 가정

    # Bucket 객체의 blob 메서드를 목(mock) 처리하여 mock_blob을 반환하게 합니다.
    mock_bucket = mocker.MagicMock()
    mock_bucket.blob.return_value = mock_blob

    # Client 객체의 bucket 메서드를 목(mock) 처리하여 mock_bucket을 반환하게 합니다.
    mock_client = mocker.MagicMock()
    mock_client.bucket.return_value = mock_bucket

    # google.cloud.storage.Client를 목(mock) 처리된 클라이언트로 대체합니다.
    mocker.patch('google.cloud.storage.Client', return_value=mock_client)

    return mock_client, mock_bucket, mock_blob

@pytest.fixture
def mock_os(mocker):
    """
    os.path.exists와 os.listdir을 목(mock) 처리하는 픽스처.
    """
    mock_exists = mocker.patch('os.path.exists')
    mock_listdir = mocker.patch('os.listdir')
    # os.path.join이 가변 인수를 받도록 변경
    mock_join = mocker.patch('os.path.join', side_effect=lambda *args: os.path.sep.join(args))
    return mock_exists, mock_listdir, mock_join

@pytest.fixture
def caplog_level_info(caplog):
    """
    pytest의 caplog 픽스처를 사용하여 로깅 레벨을 INFO로 설정합니다.
    """
    caplog.set_level(logging.INFO)
    return caplog

# --- 테스트 케이스들 ---

def test_upload_success_specific_date(mock_gcs, mock_os, caplog_level_info):
    """
    특정 날짜로 JSON 파일이 성공적으로 업로드되는지 테스트합니다.
    """
    _, mock_bucket, mock_blob = mock_gcs
    mock_exists, mock_listdir, _ = mock_os

    mock_exists.return_value = True # 디렉토리가 존재한다고 가정
    mock_listdir.return_value = ['file1.json', 'image.png', 'file2.json'] # 디렉토리 내용

    test_date = '20231225'
    local_dir = 'my_data'
    bucket_name = 'my_test_bucket'
    base_path = 'my_news'

    gcs_upload_json.upload_json_files_to_gcs(local_data_dir=local_dir, bucket_name=bucket_name, gcs_base_path=base_path, date_str=test_date)

    # GCS 클라이언트와 버킷 메서드가 올바르게 호출되었는지 확인
    mock_bucket.blob.assert_any_call(f"{base_path}/{test_date}/file1.json")
    mock_bucket.blob.assert_any_call(f"{base_path}/{test_date}/file2.json")
    assert mock_blob.upload_from_filename.call_count == 2 # 2개의 JSON 파일이 업로드됨

    # 로그 메시지 확인
    assert f"JSON 파일을 '{local_dir}'에서 'gs://{bucket_name}/{base_path}/{test_date}/'(으)로 업로드 시작." in caplog_level_info.text
    assert f"성공적으로 'file1.json'을(를) 'gs://{bucket_name}/{base_path}/{test_date}/file1.json'에 업로드했습니다." in caplog_level_info.text
    assert f"성공적으로 'file2.json'을(를) 'gs://{bucket_name}/{base_path}/{test_date}/file2.json'에 업로드했습니다." in caplog_level_info.text
    assert f"JSON 파일이 아닌 파일 건너뛰기: 'image.png'" in caplog_level_info.text


def test_upload_success_current_date(mock_gcs, mock_os, caplog_level_info, mocker):
    """
    현재 날짜로 JSON 파일이 성공적으로 업로드되는지 테스트합니다.
    """
    _, mock_bucket, mock_blob = mock_gcs
    mock_exists, mock_listdir, _ = mock_os

    mock_exists.return_value = True
    mock_listdir.return_value = ['article.json']

    # datetime.now().strftime()을 목(mock) 처리하여 특정 날짜를 반환하게 합니다.
    # gcs_uploader 모듈의 datetime을 패치 (실제 파일 경로 사용)
    # 현재 코드 구조상 (함수가 테스트 파일 내에 직접 정의됨), 'datetime.datetime'으로 직접 패치하는 것이 올바릅니다.
    mock_datetime_now = mocker.patch('datetime.datetime')
    mock_datetime_now.now.return_value.strftime.return_value = '20240101' # 고정된 현재 날짜

    local_dir = 'current_data'
    bucket_name = 'current_bucket'
    base_path = 'daily_news'

    gcs_upload_json.upload_json_files_to_gcs(local_data_dir=local_dir, bucket_name=bucket_name, gcs_base_path=base_path)

    mock_bucket.blob.assert_called_once_with(f"{base_path}/20240101/article.json")
    mock_blob.upload_from_filename.assert_called_once()
    assert f"JSON 파일을 '{local_dir}'에서 'gs://{bucket_name}/{base_path}/20240101/'(으)로 업로드 시작." in caplog_level_info.text


def test_local_dir_not_exists(mock_gcs, mock_os, caplog_level_info):
    """
    로컬 데이터 디렉토리가 존재하지 않을 때 함수가 올바르게 처리하는지 테스트합니다.
    """
    _, mock_bucket, mock_blob = mock_gcs
    mock_exists, mock_listdir, _ = mock_os

    mock_exists.return_value = False # 디렉토리가 존재하지 않음

    local_dir = 'non_existent_data'
    gcs_upload_json.upload_json_files_to_gcs(local_data_dir=local_dir)

    mock_bucket.assert_not_called() # GCS 관련 메서드는 호출되지 않아야 함
    mock_listdir.assert_not_called() # os.listdir도 호출되지 않아야 함
    assert f"로컬 데이터 디렉토리 '{local_dir}'가 존재하지 않습니다." in caplog_level_info.text
    assert mock_blob.upload_from_filename.call_count == 0


def test_no_json_files_in_dir(mock_gcs, mock_os, caplog_level_info):
    """
    디렉토리에 JSON 파일이 없을 때 함수가 올바르게 동작하는지 테스트합니다.
    """
    _, mock_bucket, mock_blob = mock_gcs
    mock_exists, mock_listdir, _ = mock_os

    mock_exists.return_value = True
    mock_listdir.return_value = ['image.png', 'document.txt'] # JSON 파일 없음

    local_dir = 'empty_json_data'
    gcs_upload_json.upload_json_files_to_gcs(local_data_dir=local_dir)

    mock_bucket.blob.assert_not_called() # blob 메서드는 호출되지 않아야 함
    mock_blob.upload_from_filename.assert_not_called() # 업로드도 발생하지 않아야 함
    assert f"JSON 파일이 아닌 파일 건너뛰기: 'image.png'" in caplog_level_info.text
    assert f"JSON 파일이 아닌 파일 건너뛰기: 'document.txt'" in caplog_level_info.text
    assert "업로드 시작" in caplog_level_info.text # 시작 로그는 찍혀야 함


def test_gcs_bucket_access_failure(mock_gcs, mock_os, caplog_level_info):
    """
    GCS 버킷 접근에 실패했을 때 함수가 올바르게 처리하는지 테스트합니다.
    """
    mock_client, _, _ = mock_gcs
    mock_exists, _, _ = mock_os

    mock_exists.return_value = True
    mock_client.bucket.side_effect = Exception("버킷 접근 권한 없음") # 버킷 접근 시 예외 발생

    test_bucket_name = "invalid_bucket"
    gcs_upload_json.upload_json_files_to_gcs(bucket_name=test_bucket_name)

    assert f"버킷 '{test_bucket_name}'에 접근할 수 없습니다: 버킷 접근 권한 없음" in caplog_level_info.text


def test_file_upload_failure(mock_gcs, mock_os, caplog_level_info):
    """
    파일 업로드 중 예외가 발생했을 때 함수가 올바르게 처리하는지 테스트합니다.
    """
    _, mock_bucket, mock_blob = mock_gcs
    mock_exists, mock_listdir, _ = mock_os

    mock_exists.return_value = True
    mock_listdir.return_value = ['fail_file.json']
    mock_blob.upload_from_filename.side_effect = Exception("권한 부족") # 업로드 시 예외 발생

    gcs_upload_json.upload_json_files_to_gcs() # 기본 인자 사용

    mock_blob.upload_from_filename.assert_called_once()
    assert f"'{'fail_file.json'}' 업로드 실패: 권한 부족" in caplog_level_info.text
    assert "업로드 시작" in caplog_level_info.text # 시작 로그는 찍혀야 함


def test_invalid_date_string_format(mock_gcs, mock_os, caplog_level_info):
    """
    잘못된 날짜 문자열 형식이 제공되었을 때 함수가 올바르게 처리하는지 테스트합니다.
    """
    mock_exists, _, _ = mock_os
    mock_exists.return_value = True

    invalid_date = '2023-12-25' # 잘못된 형식
    gcs_upload_json.upload_json_files_to_gcs(date_str=invalid_date)

    assert f"유효하지 않은 날짜 문자열 형식입니다: '{invalid_date}'. YYYYMMDD 형식이어야 합니다." in caplog_level_info.text
    assert "업로드 시작" not in caplog_level_info.text # 업로드 시작 로그는 찍히지 않아야 함