import os
import site
import pytest

from unittest.mock import MagicMock

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services import gcs_download_json

# 실행: .venv/bin/python -m pytest tests/test_gcs_download_json.py -q
# 실제 GCS 에는 접근하지 않는다. 다운로드한 파일은 tmp_path 에만 쓴다.


# --- download_gcs_posts_json_to_local ---

def test_posts_download_returns_only_downloaded_users_in_input_order(mocker):
    """반환값이 0 인 사용자만, 입력 순서대로 반환한다 (1: GCS 에 파일 없음, 2: 접근 오류 는 제외)."""
    returns = {'a_posts.json': 0, 'b_posts.json': 1, 'c_posts.json': 0, 'd_posts.json': 2, 'e_posts.json': 0}
    mock_download = mocker.patch.object(gcs_download_json, 'download_gcs_to_local',
                                        side_effect=lambda file_name, date_str=None, **kw: returns[file_name])

    result = gcs_download_json.download_gcs_posts_json_to_local(target_user_list=['e', 'b', 'a', 'd', 'c'],
                                                                target_date='20261115')

    assert result == ['e', 'a', 'c']
    assert [c.args[0] for c in mock_download.call_args_list] == [
        'e_posts.json', 'b_posts.json', 'a_posts.json', 'd_posts.json', 'c_posts.json']
    assert all(c.kwargs['date_str'] == '20261115' for c in mock_download.call_args_list)


def test_posts_download_returns_empty_list_when_nothing_downloaded(mocker):
    mocker.patch.object(gcs_download_json, 'download_gcs_to_local', return_value=1)

    assert gcs_download_json.download_gcs_posts_json_to_local(target_user_list=['a', 'b'], target_date='20261115') == []


@pytest.mark.parametrize("empty_list", [None, []])
def test_posts_download_defaults_to_target_usernames(mocker, empty_list):
    """대상 목록이 없으면(None 또는 빈 목록) TARGET_USERNAMES 를 쓰고, 반환값은 내려받은 사용자만이다."""
    mocker.patch.object(gcs_download_json.tweet_scrapper_post, 'TARGET_USERNAMES', ['x', 'y', 'z'])
    mock_download = mocker.patch.object(gcs_download_json, 'download_gcs_to_local',
                                        side_effect=lambda file_name, **kw: 0 if file_name != 'y_posts.json' else 1)

    result = gcs_download_json.download_gcs_posts_json_to_local(target_user_list=empty_list, target_date='20261115')

    assert result == ['x', 'z']
    assert mock_download.call_count == 3


# --- download_gcs_to_local (storage.Client 를 목 처리, 저장 위치는 tmp_path) ---

@pytest.fixture
def mock_storage(mocker):
    client = MagicMock()
    mocker.patch.object(gcs_download_json.storage, 'Client', return_value=client)
    return client


def test_download_returns_0_and_saves_file_under_given_directory(mock_storage, tmp_path):
    blob = mock_storage.bucket.return_value.blob.return_value
    blob.exists.return_value = True
    target_dir = tmp_path / "data"

    ret = gcs_download_json.download_gcs_to_local('u_posts.json', date_str='20261115', local_file_path=str(target_dir))

    assert ret == 0
    mock_storage.bucket.return_value.blob.assert_called_once_with('news_data/20261115/u_posts.json')
    blob.download_to_filename.assert_called_once_with(os.path.join(str(target_dir), 'u_posts.json'))
    assert target_dir.is_dir()


def test_download_returns_1_when_blob_missing(mock_storage, tmp_path):
    blob = mock_storage.bucket.return_value.blob.return_value
    blob.exists.return_value = False

    ret = gcs_download_json.download_gcs_to_local('u_posts.json', date_str='20261115', local_file_path=str(tmp_path))

    assert ret == 1
    blob.download_to_filename.assert_not_called()


def test_download_returns_2_when_bucket_access_fails(mock_storage, tmp_path):
    mock_storage.bucket.side_effect = Exception("no access")

    ret = gcs_download_json.download_gcs_to_local('u_posts.json', date_str='20261115', local_file_path=str(tmp_path))

    assert ret == 2


def test_download_reraises_when_download_fails(mock_storage, tmp_path):
    blob = mock_storage.bucket.return_value.blob.return_value
    blob.exists.return_value = True
    blob.download_to_filename.side_effect = OSError("disk error")

    with pytest.raises(OSError):
        gcs_download_json.download_gcs_to_local('u_posts.json', date_str='20261115', local_file_path=str(tmp_path))
