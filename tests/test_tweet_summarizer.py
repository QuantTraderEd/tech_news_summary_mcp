import os
import site
import json

from unittest import mock

import pytest
from google.api_core import exceptions as google_exceptions
from google.protobuf import duration_pb2
from google.rpc import error_details_pb2

# Add project root to the Python path
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# tweet_summarizer 는 import 시점에 config.json 의 GEMINI_API_KEY 를 읽고, 없으면 sys.exit(1) 한다.
if not os.path.exists(os.path.join(pjt_home_path, 'config.json')):
    pytest.skip("config.json 이 없어 tweet_summarizer 를 import 할 수 없습니다.", allow_module_level=True)

from src.services import tweet_summarizer

BASE_YMD = "20250628"
PREV_YMD = "20250627"
URL_A = "https://x.com/user1/status/1"
URL_B = "https://x.com/user1/status/2"


@pytest.fixture
def summarizer_env(tmp_path, mocker):
    """
    tweet_summarizer.main 이 임시 디렉토리에서만 파일을 읽고 쓰도록 하고,
    Gemini / GCS 호출을 모두 목(mock) 처리한다.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(tweet_summarizer, 'pjt_home_path', str(tmp_path))
    mocker.patch.object(tweet_summarizer.gcs_upload_json, 'upload_local_file_to_gcs', return_value=0)

    def fake_process_posts(input_filename, summarized_posts):
        summarized_posts.append({"url": URL_A, "id": "1", "created_at": "2025-06-28T01:00:00.000Z",
                                 "text": "long post", "title": "제목 A", "summary": "* 요약"})
        summarized_posts.append({"url": URL_B, "id": "2", "created_at": "2025-06-28T02:00:00.000Z",
                                 "text": "short post", "translated_text": "번역 B"})

    mocker.patch.object(tweet_summarizer, 'process_posts', side_effect=fake_process_posts)
    return data_dir


def _load_summarized_posts(data_dir):
    with open(data_dir / "summarized_posts.json", 'r', encoding='utf-8') as f:
        return {post['url']: post for post in json.load(f)}


def test_no_dup_tag_when_prev_agg_missing(summarizer_env, mocker):
    """전일자 agg 파일이 GCS 에 없으면 어떤 게시물에도 [중복] 태그가 붙지 않아야 한다."""
    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', return_value=1)

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['user1'])

    posts = _load_summarized_posts(summarizer_env)
    assert posts[URL_A]['title'] == "제목 A"
    assert posts[URL_B]['translated_text'] == "번역 B"


def test_dup_tag_only_for_posts_in_prev_agg(summarizer_env, mocker):
    """전일자 agg 파일에 있는 url 의 게시물에만 [중복] 태그가 붙어야 한다."""
    agg_path = summarizer_env / "summarized_posts_agg.json"

    def fake_download(file_name, date_str=None, **kwargs):
        if date_str == PREV_YMD:
            with open(agg_path, 'w', encoding='utf-8') as f:
                json.dump([{"url": URL_A, "title": "어제 수집된 A"}], f, ensure_ascii=False)
            return 0
        return 1  # 당일자 agg 는 아직 없음

    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', side_effect=fake_download)

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['user1'])

    posts = _load_summarized_posts(summarizer_env)
    assert posts[URL_A]['title'] == "[중복] 제목 A"
    assert posts[URL_B]['translated_text'] == "번역 B"


# --- call_gemini_api: 429(할당량/요청 한도 초과) 재시도 테스트 ---

def _quota_error(retry_delay_seconds=None):
    """Gemini API 가 gRPC 로 돌려주는 429(ResourceExhausted) 예외를 만든다."""
    details = []
    if retry_delay_seconds is not None:
        details.append(error_details_pb2.RetryInfo(
            retry_delay=duration_pb2.Duration(seconds=retry_delay_seconds)))
    return google_exceptions.ResourceExhausted("You exceeded your current quota", details=details)


def _ok_response(text):
    response = mock.MagicMock()
    response.text = text
    return response


@pytest.fixture
def gemini(mocker):
    """model 과 time.sleep 을 목 처리하고, 할당량 차단 상태를 초기화한다."""
    mocker.patch.object(tweet_summarizer, '_quota_blocked_until', 0.0)
    mock_model = mocker.patch.object(tweet_summarizer, 'model')
    mock_sleep = mocker.patch.object(tweet_summarizer.time, 'sleep')
    return mock_model, mock_sleep


def test_call_gemini_api_success_without_retry(gemini):
    mock_model, mock_sleep = gemini
    mock_model.generate_content.return_value = _ok_response("번역 결과")

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert mock_model.generate_content.call_count == 1
    mock_sleep.assert_not_called()


def test_call_gemini_api_retries_429_with_backoff(gemini):
    """서버가 대기 시간을 알려주지 않으면 5초, 10초... 로 늘려가며 재시도한다."""
    mock_model, mock_sleep = gemini
    mock_model.generate_content.side_effect = [_quota_error(), _quota_error(), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert mock_model.generate_content.call_count == 3
    assert [call.args[0] for call in mock_sleep.call_args_list] == [5.0, 10.0]


def test_call_gemini_api_uses_server_retry_delay(gemini):
    """429 응답에 RetryInfo 가 있으면 그 시간(+여유)만큼 기다린다."""
    mock_model, mock_sleep = gemini
    mock_model.generate_content.side_effect = [_quota_error(retry_delay_seconds=28), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    mock_sleep.assert_called_once_with(28 + tweet_summarizer.GEMINI_RETRY_DELAY_MARGIN)


def test_call_gemini_api_gives_up_after_max_attempts_and_skips_later_calls(gemini):
    """끝까지 429 면 정해진 횟수만 시도하고, 이후 호출은 API 를 부르지 않고 바로 실패 처리한다."""
    mock_model, mock_sleep = gemini
    mock_model.generate_content.side_effect = _quota_error()

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_model.generate_content.call_count == tweet_summarizer.GEMINI_MAX_ATTEMPTS
    assert [call.args[0] for call in mock_sleep.call_args_list] == [5.0, 10.0, 20.0, 40.0]

    # 차단 시간 동안: API 호출도, 대기도 없이 바로 실패
    mock_model.generate_content.reset_mock()
    mock_sleep.reset_mock()
    assert tweet_summarizer.call_gemini_api("next prompt") == tweet_summarizer.API_ERROR_TEXT
    mock_model.generate_content.assert_not_called()
    mock_sleep.assert_not_called()

    # 차단 시간이 지나면 다시 호출한다
    tweet_summarizer._quota_blocked_until = 0.0
    mock_model.generate_content.side_effect = None
    mock_model.generate_content.return_value = _ok_response("번역 결과")
    assert tweet_summarizer.call_gemini_api("later prompt") == "번역 결과"


def test_call_gemini_api_gives_up_when_server_delay_is_too_long(gemini):
    """서버 권장 대기 시간이 한도보다 길면 기다리지 않고 바로 포기한다."""
    mock_model, mock_sleep = gemini
    mock_model.generate_content.side_effect = _quota_error(retry_delay_seconds=3600)

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_model.generate_content.call_count == 1
    mock_sleep.assert_not_called()
    assert tweet_summarizer._quota_blocked_until > 0.0


def test_call_gemini_api_does_not_retry_other_errors(gemini):
    """429 가 아닌 오류는 재시도하지 않고, 이후 호출도 막지 않는다."""
    mock_model, mock_sleep = gemini
    mock_model.generate_content.side_effect = google_exceptions.InvalidArgument("bad request")

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_model.generate_content.call_count == 1
    mock_sleep.assert_not_called()
    assert tweet_summarizer._quota_blocked_until == 0.0
