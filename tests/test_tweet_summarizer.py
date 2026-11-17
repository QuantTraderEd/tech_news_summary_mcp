import os
import site
import json

import httpx2
import pytest
from google.genai import errors, types

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

    def fake_process_posts(input_filename, summarized_posts, reuse_posts=None, stats=None):
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


# --- call_gemini_api: 시간 한도 / 재시도 테스트 ---

class FakeTime:
    """tweet_summarizer 가 쓰는 time 모듈 대역. sleep 하면 실제로 기다리지 않고 시계만 앞으로 간다."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _api_error(code, status, message, details=None):
    """Gemini API 오류 응답 본문(JSON)으로 google-genai 가 올리는 실제 예외(4xx: ClientError, 5xx: ServerError)를 만든다."""
    body = {"error": {"code": code, "message": message, "status": status}}
    if details is not None:
        body["error"]["details"] = details
    error_cls = errors.ClientError if code < 500 else errors.ServerError
    return error_cls(code, body)


def _quota_error(retry_delay_seconds=None):
    """Gemini API 가 돌려주는 429(RESOURCE_EXHAUSTED) 예외를 만든다. 대기 시간을 주면 RetryInfo 를 본문에 넣는다."""
    details = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                         "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
        {"@type": "type.googleapis.com/google.rpc.Help",
         "links": [{"description": "Learn more about Gemini API quotas",
                    "url": "https://ai.google.dev/gemini-api/docs/rate-limits"}]},
    ]
    if retry_delay_seconds is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": f"{retry_delay_seconds}s"})
    return _api_error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota", details)


def _overloaded_error():
    """503(UNAVAILABLE): 서버 과부하"""
    return _api_error(503, "UNAVAILABLE", "The model is overloaded. Please try again later.")


def _ok_response(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        finish_reason=types.FinishReason.STOP)])


@pytest.fixture
def gemini(mocker):
    """Gemini 클라이언트와 time 을 대역으로 바꾸고, API 차단 상태를 초기화한다."""
    fake_time = FakeTime()
    mocker.patch.object(tweet_summarizer, 'time', fake_time)
    mocker.patch.object(tweet_summarizer, '_gemini_blocked_until', 0.0)
    mock_client = mocker.patch.object(tweet_summarizer, 'client')
    return mock_client.models, fake_time


def _timeouts(mock_models):
    """각 API 호출에 넘긴 응답 대기 시간(timeout) 목록. SDK 에는 밀리초로 넘기므로 초로 바꿔서 돌려준다."""
    return [call.kwargs["config"].http_options.timeout / 1000 for call in mock_models.generate_content.call_args_list]


def test_call_gemini_api_success_without_retry(gemini):
    mock_models, fake_time = gemini
    mock_models.generate_content.return_value = _ok_response("번역 결과")

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    mock_models.generate_content.assert_called_once()
    kwargs = mock_models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert kwargs["contents"] == "prompt"
    # 응답 대기 시간은 호출당 시간 한도(밀리초)로 제한하고, SDK 자체 재시도와 자동 이어받기(continuation)는 끈다.
    config = kwargs["config"]
    assert config.http_options.timeout == int(tweet_summarizer.GEMINI_CALL_TIME_LIMIT * 1000)
    assert config.http_options.retry_options.attempts == 1
    assert config.automatic_continuation is False
    assert fake_time.sleeps == []


def test_call_gemini_api_retries_429_with_backoff(gemini):
    """서버가 대기 시간을 알려주지 않으면 10초, 20초... 로 늘려가며 재시도한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [_quota_error(), _quota_error(), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert mock_models.generate_content.call_count == 3
    assert fake_time.sleeps == [10.0, 20.0]
    # 재시도할수록 남은 시간만큼만 응답을 기다린다.
    assert _timeouts(mock_models) == [170.0, 160.0, 140.0]


def test_call_gemini_api_uses_server_retry_delay(gemini):
    """429 응답에 RetryInfo 가 있으면 그 시간(+여유)만큼 기다린다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [_quota_error(retry_delay_seconds=28), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [28 + tweet_summarizer.GEMINI_RETRY_DELAY_MARGIN]


@pytest.mark.parametrize("retry_delay, expected", [
    ("28s", 28.0),
    ("28.5s", 28.5),
    ("0.500s", 0.5),
])
def test_get_server_retry_delay_parses_retry_info(retry_delay, expected):
    """RetryInfo 의 retryDelay("28s", "0.500s" 같은 문자열)를 초 단위 숫자로 읽는다."""
    exc = _api_error(429, "RESOURCE_EXHAUSTED", "quota", [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}])

    assert tweet_summarizer._get_server_retry_delay(exc) == expected


@pytest.mark.parametrize("body", [
    {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"}},                   # details 없음
    {"error": {"code": 429, "details": {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                        "retryDelay": "28s"}}},                                      # 목록이 아님
    {"error": {"code": 429, "details": ["RetryInfo", 28]}},                                          # 항목이 dict 가 아님
    {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo"}]}},    # retryDelay 없음
    {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                         "retryDelay": 28}]}},                                       # 문자열이 아님
    {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                         "retryDelay": "28"}]}},                                     # 단위(s) 없음
    {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                         "retryDelay": "abcs"}]}},                                   # 숫자 아님
    {"error": {"code": 429, "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                         "retryDelay": "0s"}]}},                                     # 0 이하
    {"error": "Too Many Requests"},                                                                  # error 가 dict 아님
    {"message": "Too Many Requests", "status": "Too Many Requests"},                                 # 본문이 JSON 아님
])
def test_call_gemini_api_uses_default_backoff_when_retry_info_is_missing_or_malformed(gemini, body):
    """429 응답에서 대기 시간을 읽을 수 없으면 예외 없이 기본 대기(10초부터 2배)로 재시도한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [errors.ClientError(429, body), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [tweet_summarizer.GEMINI_RETRY_BASE_DELAY]


def test_call_gemini_api_retries_503(gemini):
    """503(서버 과부하)도 SDK 대신 이 함수가 시간 한도 안에서 재시도한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [_overloaded_error(), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [10.0]


def test_call_gemini_api_gives_up_after_max_attempts_and_skips_later_calls(gemini):
    """끝까지 429 면 정해진 횟수만 시도하고, 이후 호출은 API 를 부르지 않고 바로 실패 처리한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = _quota_error()
    started_at = fake_time.now

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == tweet_summarizer.GEMINI_MAX_ATTEMPTS
    assert fake_time.sleeps == [10.0, 20.0, 40.0]
    assert fake_time.now - started_at <= tweet_summarizer.GEMINI_CALL_TIME_LIMIT

    # 차단 시간 동안: API 호출도, 대기도 없이 바로 실패
    mock_models.generate_content.reset_mock()
    fake_time.sleeps.clear()
    assert tweet_summarizer.call_gemini_api("next prompt") == tweet_summarizer.API_ERROR_TEXT
    mock_models.generate_content.assert_not_called()
    assert fake_time.sleeps == []

    # 차단 시간이 지나면 다시 호출한다
    fake_time.now += tweet_summarizer.GEMINI_COOLDOWN
    mock_models.generate_content.side_effect = None
    mock_models.generate_content.return_value = _ok_response("번역 결과")
    assert tweet_summarizer.call_gemini_api("later prompt") == "번역 결과"


def test_call_gemini_api_stops_retrying_when_time_limit_would_be_exceeded(gemini):
    """서버가 매번 60초씩 기다리라고 하면, 시간 한도를 넘기는 세 번째 대기는 하지 않고 포기한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = _quota_error(retry_delay_seconds=60)
    started_at = fake_time.now

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert fake_time.sleeps == [61.0, 61.0]
    assert mock_models.generate_content.call_count == 3
    assert fake_time.now - started_at <= tweet_summarizer.GEMINI_CALL_TIME_LIMIT
    assert tweet_summarizer._gemini_blocked_until > fake_time.now


def test_call_gemini_api_finishes_within_time_limit_even_when_api_is_slow(gemini):
    """API 응답이 느려도(매번 50초 뒤 429) 응답 대기 + 재시도 대기 합계가 시간 한도를 넘지 않는다."""
    mock_models, fake_time = gemini

    def slow_quota_error(model, contents, config):
        timeout = config.http_options.timeout / 1000
        if timeout <= 50:
            fake_time.now += timeout
            raise httpx2.ReadTimeout("timed out")
        fake_time.now += 50
        raise _quota_error()

    mock_models.generate_content.side_effect = slow_quota_error
    started_at = fake_time.now

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert _timeouts(mock_models) == [170.0, 110.0, 40.0]
    assert fake_time.now - started_at <= tweet_summarizer.GEMINI_CALL_TIME_LIMIT
    assert tweet_summarizer.GEMINI_CALL_TIME_LIMIT < 180


def test_call_gemini_api_gives_up_when_server_delay_is_too_long(gemini):
    """서버 권장 대기 시간이 한도보다 길면 기다리지 않고 바로 포기한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = _quota_error(retry_delay_seconds=3600)

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer._gemini_blocked_until > fake_time.now


@pytest.mark.parametrize("timeout_error", [
    httpx2.ReadTimeout("timed out"),                                                          # 클라이언트 쪽 응답 대기 시간 초과
    httpx2.ConnectTimeout("timed out"),                                                       # 연결 중 시간 초과 (연결 오류가 아님)
    _api_error(504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete."),  # 서버 쪽 시간 초과
], ids=["client_timeout", "connect_timeout", "server_504"])
def test_call_gemini_api_blocks_later_calls_after_timeout(gemini, timeout_error):
    """API 가 시간 한도 안에 응답하지 않으면 재시도하지 않고, 이후 호출도 건너뛴다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = timeout_error

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer.call_gemini_api("next prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1


@pytest.mark.parametrize("connection_error", [
    httpx2.ConnectError("[Errno 111] Connection refused"),                       # 연결 실패
    httpx2.ReadError("[Errno 104] Connection reset by peer"),                    # 응답을 받는 중 끊김
    httpx2.RemoteProtocolError("Server disconnected without sending a response."),  # 서버가 응답 없이 연결을 닫음
], ids=["connect_error", "read_error", "remote_protocol_error"])
def test_call_gemini_api_retries_connection_errors_like_503(gemini, connection_error):
    """연결 오류는 503 처럼 10초, 20초... 로 늘려가며 시간 한도 안에서 재시도한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [connection_error, connection_error, _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert mock_models.generate_content.call_count == 3
    assert fake_time.sleeps == [10.0, 20.0]
    assert _timeouts(mock_models) == [170.0, 160.0, 140.0]
    assert tweet_summarizer._gemini_blocked_until == 0.0


def test_call_gemini_api_gives_up_after_max_attempts_on_connection_errors(gemini):
    """끝까지 연결되지 않으면 정해진 횟수만 시도하고 포기하며, 이후 호출은 API 를 부르지 않는다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = httpx2.ConnectError("[Errno 111] Connection refused")
    started_at = fake_time.now

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == tweet_summarizer.GEMINI_MAX_ATTEMPTS
    assert fake_time.sleeps == [10.0, 20.0, 40.0]
    assert fake_time.now - started_at <= tweet_summarizer.GEMINI_CALL_TIME_LIMIT
    assert tweet_summarizer._gemini_blocked_until > fake_time.now

    mock_models.generate_content.reset_mock()
    assert tweet_summarizer.call_gemini_api("next prompt") == tweet_summarizer.API_ERROR_TEXT
    mock_models.generate_content.assert_not_called()


def test_gemini_timeout_and_connection_errors_do_not_overlap():
    """시간 초과 예외와 연결 오류 예외가 겹치면 except 순서에 따라 잘못 분류되므로, 두 묶음은 서로 하위 클래스가 아니어야 한다."""
    assert httpx2.TimeoutException in tweet_summarizer.GEMINI_TIMEOUT_ERRORS
    assert httpx2.NetworkError in tweet_summarizer.GEMINI_CONNECTION_ERRORS
    for timeout_cls in tweet_summarizer.GEMINI_TIMEOUT_ERRORS:
        for connection_cls in tweet_summarizer.GEMINI_CONNECTION_ERRORS:
            assert not issubclass(timeout_cls, connection_cls)
            assert not issubclass(connection_cls, timeout_cls)


@pytest.mark.parametrize("error", [
    _api_error(400, "INVALID_ARGUMENT", "bad request"),
    errors.UnknownApiResponseError("Failed to parse response as JSON."),
], ids=["bad_request_400", "unparsable_response"])
def test_call_gemini_api_does_not_retry_other_errors(gemini, error):
    """재시도 대상이 아닌 오류는 재시도하지 않고, 이후 호출도 막지 않는다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = error

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == 0.0


@pytest.mark.parametrize("response", [
    types.GenerateContentResponse(                                    # 프롬프트가 차단되어 후보가 없음
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY)),
    types.GenerateContentResponse(                                    # 후보는 있지만 안전 필터로 내용이 비어 있음
        candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]),
], ids=["prompt_blocked", "candidate_without_content"])
def test_call_gemini_api_returns_error_text_when_response_has_no_text(gemini, response):
    """응답에 텍스트가 없으면(response.text 가 None) None 대신 API_ERROR_TEXT 를 돌려주고, 재시도·차단은 하지 않는다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.return_value = response
    assert response.text is None

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == 0.0


@pytest.mark.parametrize("time_limit, expected_timeout_ms", [
    (1.9999, 1999),  # 밀리초로 바꿀 때 내림해서 시간 한도를 넘겨 기다리지 않는다
    (0.0005, None),  # 1밀리초도 남지 않으면 timeout=0(무한 대기)으로 호출하지 않는다
])
def test_call_gemini_api_converts_remaining_time_to_positive_milliseconds(gemini, mocker, time_limit, expected_timeout_ms):
    """남은 시간을 SDK 의 응답 대기 시간(밀리초 정수)으로 넘길 때 내림하고, 0 이하는 넘기지 않는다."""
    mock_models, fake_time = gemini
    mocker.patch.object(tweet_summarizer, 'GEMINI_CALL_TIME_LIMIT', time_limit)
    mock_models.generate_content.return_value = _ok_response("번역 결과")

    result = tweet_summarizer.call_gemini_api("prompt")

    if expected_timeout_ms is None:
        assert result == tweet_summarizer.API_ERROR_TEXT
        mock_models.generate_content.assert_not_called()
    else:
        assert result == "번역 결과"
        assert mock_models.generate_content.call_args.kwargs["config"].http_options.timeout == expected_timeout_ms


# --- call_gemini_api: 경계 조건 (클라이언트 대역) ---

def test_call_gemini_api_retries_when_server_delay_is_exactly_max_delay(gemini):
    """서버 권장 대기 시간이 한도(60초)와 같으면 포기하지 않고 61초 기다린 뒤 재시도한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [_quota_error(retry_delay_seconds=60), _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [60 + tweet_summarizer.GEMINI_RETRY_DELAY_MARGIN]


def test_call_gemini_api_gives_up_when_server_delay_is_just_over_max_delay(gemini):
    """서버 권장 대기 시간이 한도(60초)를 조금이라도 넘으면 기다리지 않고 포기하고 쿨다운에 들어간다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = _quota_error(retry_delay_seconds="60.001")

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == fake_time.now + tweet_summarizer.GEMINI_COOLDOWN


@pytest.mark.parametrize("elapsed, expect_retry", [
    (150.0, True),     # 150 + 대기 10 + 최소 시도 시간 10 == 170: 한도 안이므로 재시도
    (150.001, False),  # 한도를 넘으므로 포기
])
def test_call_gemini_api_retry_boundary_against_time_limit(gemini, elapsed, expect_retry):
    """남은 시간이 '대기 + 재시도 최소 시간' 보다 적으면 재시도하지 않고 포기한다(같으면 재시도)."""
    mock_models, fake_time = gemini
    started_at = fake_time.now

    def slow_then_ok(model, contents, config):
        if mock_models.generate_content.call_count == 1:
            fake_time.now = started_at + elapsed
            raise _overloaded_error()
        return _ok_response("번역 결과")

    mock_models.generate_content.side_effect = slow_then_ok

    result = tweet_summarizer.call_gemini_api("prompt")

    if expect_retry:
        assert result == "번역 결과"
        assert fake_time.sleeps == [10.0]
        # 두 번째 시도의 응답 대기 시간은 남은 10초(10000ms)
        assert mock_models.generate_content.call_args.kwargs["config"].http_options.timeout == 10_000
        assert tweet_summarizer._gemini_blocked_until == 0.0
    else:
        assert result == tweet_summarizer.API_ERROR_TEXT
        assert mock_models.generate_content.call_count == 1
        assert fake_time.sleeps == []
        assert tweet_summarizer._gemini_blocked_until == fake_time.now + tweet_summarizer.GEMINI_COOLDOWN


def test_call_gemini_api_cooldown_lasts_exactly_cooldown_seconds(gemini):
    """포기한 뒤 GEMINI_COOLDOWN(600초) 직전까지는 호출을 건너뛰고, 600초가 지나면 다시 호출한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = httpx2.ReadTimeout("timed out")

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    gave_up_at = fake_time.now
    assert tweet_summarizer.GEMINI_COOLDOWN == 600.0

    mock_models.generate_content.reset_mock()
    mock_models.generate_content.side_effect = None
    mock_models.generate_content.return_value = _ok_response("번역 결과")

    fake_time.now = gave_up_at + tweet_summarizer.GEMINI_COOLDOWN - 0.001
    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    mock_models.generate_content.assert_not_called()

    fake_time.now = gave_up_at + tweet_summarizer.GEMINI_COOLDOWN
    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    mock_models.generate_content.assert_called_once()


@pytest.mark.parametrize("code, status", [
    (408, "REQUEST_TIMEOUT"),
    (500, "INTERNAL"),
    (502, "BAD_GATEWAY"),
    (403, "PERMISSION_DENIED"),
])
def test_call_gemini_api_does_not_retry_other_status_codes(gemini, code, status):
    """429·503 이 아닌 상태 코드(SDK 기본 재시도 대상인 408·500·502 포함)는 재시도·쿨다운 없이 실패한다."""
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = _api_error(code, status, "error")

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert mock_models.generate_content.call_count == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == 0.0


def test_call_gemini_api_handles_httpx_backend_errors(gemini):
    """GOOGLE_GENAI_HTTP_CLIENT=httpx 로 httpx 백엔드를 쓸 때의 예외도 같은 분류로 처리한다."""
    httpx = pytest.importorskip("httpx")
    mock_models, fake_time = gemini
    mock_models.generate_content.side_effect = [httpx.ConnectError("refused"), httpx.RemoteProtocolError("closed"),
                                                _ok_response("번역 결과")]

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [10.0, 20.0]

    mock_models.generate_content.side_effect = httpx.ReadTimeout("timed out")
    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert tweet_summarizer._gemini_blocked_until == fake_time.now + tweet_summarizer.GEMINI_COOLDOWN


# --- call_gemini_api: 실제 SDK 경로 테스트 (가짜 HTTP 전송 계층) ---
#
# 위 테스트들은 tweet_summarizer.client 를 통째로 대역으로 바꾸므로, SDK 가 실제로 어떤 예외를 올리고
# 요청 설정(timeout, retry_options, automatic_continuation)을 어떻게 적용하는지는 확인하지 못한다.
# 아래 테스트는 더미 API 키와 httpx2.MockTransport 를 쓰는 실제 genai.Client 로 SDK 코드를 그대로 거친다.
#   - 전송 계층 주입: HttpOptions(client_args={'transport': ...}) 는 그대로 httpx2.Client(**client_args) 에
#     전달된다 (google/genai/_api_client.py 의 BaseApiClient.__init__: client_args -> sync_client_cls(**client_args)).
#     MockTransport 가 httpx2 객체라 백엔드도 httpx2 로 정해진다 (_detect_http_backend).
#   - SDK 의 재시도 대기(tenacity)는 tenacity.nap.sleep -> time.sleep 을 부르므로 tenacity.nap.time 을 대역으로 바꾼다.

import tenacity  # noqa: E402
from google import genai  # noqa: E402

DUMMY_API_KEY = "dummy-api-key-for-unit-test-0123456789"


class _ForbiddenModels:
    """대역을 깜빡한 테스트가 실제 Gemini API 를 부르지 않게 막는 대역. 호출되면 기록하고 예외를 던진다."""

    def __init__(self):
        self.calls = []

    def generate_content(self, *args, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("테스트에서 실제 Gemini 클라이언트를 호출했습니다.")


class _ForbiddenClient:
    def __init__(self):
        self.models = _ForbiddenModels()


@pytest.fixture(autouse=True)
def _forbid_real_gemini_calls(monkeypatch):
    """
    모든 테스트에 자동 적용: 모듈이 import 시 실제 키로 만든 client 를 '호출되면 실패하는' 대역으로 바꾸고,
    httpx2 / httpx 의 실제 네트워크 전송도 막는다. call_gemini_api 는 모든 예외를 삼키므로,
    대역이 호출됐는지는 테스트가 끝난 뒤 따로 확인한다.
    """
    forbidden = _ForbiddenClient()
    monkeypatch.setattr(tweet_summarizer, "client", forbidden)

    def _no_network(self, request):
        raise RuntimeError(f"테스트에서 실제 네트워크 요청을 보냈습니다: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", _no_network)
    try:
        import httpx
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    except ImportError:
        pass

    yield
    assert forbidden.models.calls == [], "대역 없이 tweet_summarizer.client 를 호출한 테스트가 있습니다."


def _http(status, body):
    """가짜 서버가 돌려줄 응답(상태 코드 + JSON 본문). 요청마다 새 Response 를 만든다."""
    return lambda: httpx2.Response(status, json=body)


def _ok_body(text, finish_reason="STOP", **candidate_fields):
    candidate = {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": finish_reason}
    candidate.update(candidate_fields)
    return {"candidates": [candidate]}


def _error_body(code, status, message, details=None):
    body = {"error": {"code": code, "message": message, "status": status}}
    if details is not None:
        body["error"]["details"] = details
    return body


def _quota_body(retry_delay=None):
    """현실적인 429 응답 본문: QuotaFailure, Help, (선택) RetryInfo 를 담는다."""
    details = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
         "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_free_tier_requests",
                         "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier",
                         "quotaDimensions": {"location": "global", "model": "gemini-3.5-flash-lite"},
                         "quotaValue": "15"}]},
        {"@type": "type.googleapis.com/google.rpc.Help",
         "links": [{"description": "Learn more about Gemini API quotas",
                    "url": "https://ai.google.dev/gemini-api/docs/rate-limits"}]},
    ]
    if retry_delay is not None:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay})
    return _error_body(429, "RESOURCE_EXHAUSTED",
                       "You exceeded your current quota, please check your plan and billing details.", details)


OVERLOADED_BODY = _error_body(503, "UNAVAILABLE", "The model is overloaded. Please try again later.")


class FakeGeminiServer:
    """
    httpx2.MockTransport 의 핸들러. 받은 요청을 기록하고, 정해 둔 응답이나 예외를 차례로 돌려준다.
    목록을 다 쓰면 마지막 항목을 계속 돌려준다(예: '계속 429').
    """

    def __init__(self, fake_time=None, seconds_per_request=0.0):
        self.items = []
        self.requests = []
        self.fake_time = fake_time
        self.seconds_per_request = seconds_per_request

    def respond(self, *items):
        """items: 예외 인스턴스(전송 계층에서 던짐) 또는 _http(...) 로 만든 응답 함수"""
        self.items = list(items)

    def handler(self, request):
        self.requests.append(request)
        if self.fake_time is not None and self.seconds_per_request:
            # 서버가 요청마다 seconds_per_request 초를 쓴다. 응답 대기 시간(timeout)을 넘기면 실제 전송 계층처럼
            # timeout 만큼 지난 뒤 ReadTimeout 을 던진다.
            timeout = request.extensions["timeout"]["read"]
            if timeout is not None and self.seconds_per_request >= timeout:
                self.fake_time.now += timeout
                raise httpx2.ReadTimeout("The read operation timed out")
            self.fake_time.now += self.seconds_per_request
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, BaseException):
            raise item
        return item()

    @property
    def read_timeouts(self):
        """각 요청에 실제로 적용된 응답 대기 시간(초)"""
        return [request.extensions["timeout"]["read"] for request in self.requests]

    @property
    def server_timeout_headers(self):
        return [request.headers.get("x-server-timeout") for request in self.requests]


class FakeSleep:
    """tenacity.nap 이 쓰는 time 모듈 대역. SDK 자체 재시도 대기를 기록만 한다."""

    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def _make_sdk_client(server, client_retry_options=None):
    """가짜 전송 계층으로만 요청하는 실제 genai.Client"""
    return genai.Client(
        api_key=DUMMY_API_KEY,
        vertexai=False,
        http_options=types.HttpOptions(
            client_args={"transport": httpx2.MockTransport(server.handler)},
            retry_options=client_retry_options,
        ),
    )


@pytest.fixture
def sdk_gemini(mocker, monkeypatch):
    """
    tweet_summarizer.client 를 가짜 전송 계층을 쓰는 실제 genai.Client 로 바꾼다.
    클라이언트에는 일부러 SDK 재시도(5회, SDK 기본 재시도 상태 코드 전체)를 켜 두어,
    요청별 설정(retry_options attempts=1)이 클라이언트 설정을 이기는지도 함께 확인한다.
    """
    for name in ("GOOGLE_GENAI_HTTP_CLIENT", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_CLIENT_MODE",
                 "GOOGLE_GEMINI_BASE_URL", "GOOGLE_GENAI_API_VERSION"):
        monkeypatch.delenv(name, raising=False)

    fake_time = FakeTime()
    mocker.patch.object(tweet_summarizer, 'time', fake_time)
    mocker.patch.object(tweet_summarizer, '_gemini_blocked_until', 0.0)
    sdk_sleep = FakeSleep()
    mocker.patch.object(tenacity.nap, 'time', sdk_sleep)

    server = FakeGeminiServer(fake_time=fake_time)
    sdk_client = _make_sdk_client(server, client_retry_options=types.HttpRetryOptions(
        attempts=5, initial_delay=1.0, max_delay=2.0))
    mocker.patch.object(tweet_summarizer, 'client', sdk_client)
    return server, fake_time, sdk_sleep


def test_sdk_request_shape_and_success(sdk_gemini):
    """정상 응답: 모델·프롬프트·API 키가 요청에 실리고, 응답 텍스트를 그대로 돌려준다."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(_http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("트윗 프롬프트") == "번역 결과"
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")
    assert request.headers["x-goog-api-key"] == DUMMY_API_KEY
    body = json.loads(request.content)
    assert body["contents"] == [{"parts": [{"text": "트윗 프롬프트"}], "role": "user"}]
    assert server.read_timeouts == [170.0]
    assert server.server_timeout_headers == ["170"]
    assert fake_time.sleeps == [] and sdk_sleep.sleeps == []


def test_sdk_429_retry_info_from_real_exception_waits_server_delay(sdk_gemini):
    """현실적인 429 본문의 RetryInfo(28s) 를 SDK 가 올린 예외에서 읽어 28 + 1 초 기다린 뒤 재시도한다."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(_http(429, _quota_body("28s")), _http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [28 + tweet_summarizer.GEMINI_RETRY_DELAY_MARGIN]
    assert len(server.requests) == 2
    assert sdk_sleep.sleeps == []
    # 두 번째 요청은 29초를 기다린 뒤라 남은 141초만 기다린다
    assert server.read_timeouts == [170.0, 141.0]
    assert server.server_timeout_headers == ["170", "141"]


def test_sdk_429_fractional_retry_info(sdk_gemini):
    """retryDelay 가 소수("0.500s")여도 읽는다."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(_http(429, _quota_body("0.500s")), _http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert fake_time.sleeps == [1.5]


def test_sdk_429_retry_info_too_long_gives_up(sdk_gemini):
    """일일 할당량 소진처럼 서버 권장 대기 시간이 60초를 넘으면 요청 1회 후 포기하고 쿨다운한다."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(_http(429, _quota_body("3600s")))

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert len(server.requests) == 1
    assert fake_time.sleeps == [] and sdk_sleep.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == fake_time.now + tweet_summarizer.GEMINI_COOLDOWN


@pytest.mark.parametrize("failure, expected_requests, expected_sleeps", [
    (_http(429, _quota_body()), 4, [10.0, 20.0, 40.0]),
    (_http(503, OVERLOADED_BODY), 4, [10.0, 20.0, 40.0]),
    (httpx2.ConnectError("[Errno 111] Connection refused"), 4, [10.0, 20.0, 40.0]),
    (httpx2.ReadError("[Errno 104] Connection reset by peer"), 4, [10.0, 20.0, 40.0]),
    (httpx2.RemoteProtocolError("Server disconnected without sending a response."), 4, [10.0, 20.0, 40.0]),
    (httpx2.ReadTimeout("The read operation timed out"), 1, []),
    (httpx2.ConnectTimeout("timed out"), 1, []),
    (_http(504, _error_body(504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete.")), 1, []),
], ids=["429", "503", "connect_error", "read_error", "remote_protocol_error", "read_timeout", "connect_timeout",
        "504"])
def test_sdk_sends_exactly_one_http_request_per_attempt(sdk_gemini, failure, expected_requests, expected_sleeps):
    """
    실패가 계속될 때 시도 한 번에 HTTP 요청은 정확히 1회다(SDK 자체 재시도가 꺼져 있음).
    클라이언트에 SDK 재시도(5회)를 켜 두었으므로, 요청별 설정이 이기지 않으면 요청 수가 늘어난다.
    재시도 대상은 4회 시도 후, 시간 초과·504 는 1회 후 포기하며 둘 다 쿨다운한다.
    """
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(failure)
    started_at = fake_time.now

    result = tweet_summarizer.call_gemini_api("prompt")

    assert result == tweet_summarizer.API_ERROR_TEXT
    assert len(server.requests) == expected_requests
    assert fake_time.sleeps == expected_sleeps
    assert sdk_sleep.sleeps == []
    assert fake_time.now - started_at <= tweet_summarizer.GEMINI_CALL_TIME_LIMIT
    assert tweet_summarizer._gemini_blocked_until == fake_time.now + tweet_summarizer.GEMINI_COOLDOWN

    # 쿨다운 동안에는 HTTP 요청을 보내지 않는다
    assert tweet_summarizer.call_gemini_api("next prompt") == tweet_summarizer.API_ERROR_TEXT
    assert len(server.requests) == expected_requests


@pytest.mark.parametrize("failure", [
    _http(429, _quota_body()),
    _http(503, OVERLOADED_BODY),
    httpx2.ConnectError("[Errno 111] Connection refused"),
    httpx2.RemoteProtocolError("Server disconnected without sending a response."),
], ids=["429", "503", "connect_error", "remote_protocol_error"])
def test_sdk_retryable_failure_then_success(sdk_gemini, failure):
    """재시도 대상 오류 두 번 뒤 성공하면 결과를 돌려주고 쿨다운하지 않는다. 대기는 이 함수만 한다."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(failure, failure, _http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert len(server.requests) == 3
    assert fake_time.sleeps == [10.0, 20.0]
    assert sdk_sleep.sleeps == []
    assert server.read_timeouts == [170.0, 160.0, 140.0]
    assert tweet_summarizer._gemini_blocked_until == 0.0


@pytest.mark.parametrize("failure", [
    _http(400, _error_body(400, "INVALID_ARGUMENT", "Request contains an invalid argument.")),
    _http(500, _error_body(500, "INTERNAL", "An internal error has occurred.")),
    _http(408, _error_body(408, "REQUEST_TIMEOUT", "Request timeout.")),
    lambda: httpx2.Response(200, text="<html>not json</html>"),
], ids=["400", "500", "408", "unparsable_200"])
def test_sdk_other_errors_fail_without_retry_or_cooldown(sdk_gemini, failure):
    """그 밖의 오류는 요청 1회 후 재시도·쿨다운 없이 API_ERROR_TEXT. (408·500 은 SDK 기본 재시도 대상이다)"""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(failure)

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert len(server.requests) == 1
    assert fake_time.sleeps == [] and sdk_sleep.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == 0.0


def test_sdk_timeout_header_and_transport_timeout_follow_remaining_time(sdk_gemini):
    """
    시도마다 SDK 에 넘긴 남은 시간(밀리초)이 실제 전송 계층의 timeout(초)과 X-Server-Timeout 헤더(초, 올림)로
    전달되고, 재시도할수록 줄어든다. 요청마다 서버가 5.5초씩 쓰는 경우.
    """
    server, fake_time, sdk_sleep = sdk_gemini
    server.seconds_per_request = 5.5
    server.respond(_http(503, OVERLOADED_BODY), _http(503, OVERLOADED_BODY), _http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    # 170 -> 170 - 5.5 - 10 = 154.5 -> 154.5 - 5.5 - 20 = 129
    assert server.read_timeouts == [170.0, 154.5, 129.0]
    assert server.server_timeout_headers == ["170", "155", "129"]
    for request in server.requests:
        assert set(request.extensions["timeout"].values()) == {request.extensions["timeout"]["read"]}


def test_sdk_timeout_is_rounded_down_to_milliseconds(sdk_gemini, mocker):
    """남은 시간을 밀리초로 내림해 넘긴다: 1.9999초 -> 1999ms -> 전송 계층 1.999초, 헤더 2초"""
    server, fake_time, sdk_sleep = sdk_gemini
    mocker.patch.object(tweet_summarizer, 'GEMINI_CALL_TIME_LIMIT', 1.9999)
    server.respond(_http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("prompt") == "번역 결과"
    assert server.read_timeouts == [1.999]
    assert server.server_timeout_headers == ["2"]


def test_sdk_never_sends_request_without_timeout(sdk_gemini, mocker):
    """남은 시간이 1밀리초 미만이면 timeout 없이(무한 대기) 요청하지 않고 포기한다."""
    server, fake_time, sdk_sleep = sdk_gemini
    mocker.patch.object(tweet_summarizer, 'GEMINI_CALL_TIME_LIMIT', 0.0009)
    server.respond(_http(200, _ok_body("번역 결과")))

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert server.requests == []


def test_sdk_slow_server_never_gets_unbounded_timeout(sdk_gemini):
    """
    서버가 매번 100초를 쓰고 연결을 끊으면: 모든 요청에 양수 timeout 이 붙고, 두 번째 요청은 남은 60초에서
    시간 초과로 끝나며, 전체 소요 시간이 호출당 한도(170초)를 넘지 않는다.
    """
    server, fake_time, sdk_sleep = sdk_gemini
    server.seconds_per_request = 100.0
    server.respond(httpx2.RemoteProtocolError("Server disconnected without sending a response."))
    started_at = fake_time.now

    assert tweet_summarizer.call_gemini_api("prompt") == tweet_summarizer.API_ERROR_TEXT
    assert all(timeout is not None and timeout > 0 for timeout in server.read_timeouts)
    assert all(header is not None for header in server.server_timeout_headers)
    # 170 -> 100초 사용 + 10초 대기 -> 남은 60초로 재시도 -> 60초 뒤 시간 초과 -> 포기
    assert server.read_timeouts == [170.0, 60.0]
    assert fake_time.sleeps == [10.0]
    assert fake_time.now - started_at == pytest.approx(tweet_summarizer.GEMINI_CALL_TIME_LIMIT)
    assert tweet_summarizer._gemini_blocked_until == fake_time.now + tweet_summarizer.GEMINI_COOLDOWN


@pytest.mark.parametrize("body", [
    {"promptFeedback": {"blockReason": "SAFETY"}},                                         # 프롬프트 차단: 후보 없음
    {"candidates": [{"finishReason": "SAFETY"}]},                                          # 후보에 내용 없음
    {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "f", "args": {}}}]},
                     "finishReason": "STOP"}]},                                             # 텍스트 파트 없음
    {"candidates": [{"content": {"role": "model", "parts": [{"text": "생각", "thought": True}]},
                     "finishReason": "STOP"}]},                                             # 생각(thought) 파트만 있음
], ids=["prompt_blocked", "candidate_without_content", "no_text_part", "thought_only"])
def test_sdk_response_without_text_returns_error_text(sdk_gemini, body, caplog):
    """SDK 가 response.text 로 None 을 돌려주는 응답이면 None 대신 API_ERROR_TEXT. 재시도·쿨다운 없음."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(_http(200, body))

    result = tweet_summarizer.call_gemini_api("prompt")

    assert result == tweet_summarizer.API_ERROR_TEXT
    assert len(server.requests) == 1
    assert fake_time.sleeps == []
    assert tweet_summarizer._gemini_blocked_until == 0.0
    assert "텍스트가 없습니다" in caplog.text


def test_sdk_does_not_follow_continuation(sdk_gemini):
    """응답이 CONTINUATION 으로 끝나도 SDK 가 후속 요청을 보내지 않는다(automatic_continuation=False)."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(_http(200, _ok_body("앞부분", finish_reason="CONTINUATION", continuationToken="YWJj")),
                   _http(200, _ok_body("뒷부분")))

    assert tweet_summarizer.call_gemini_api("prompt") == "앞부분"
    assert len(server.requests) == 1


@pytest.mark.parametrize("transport_error, expected_cls", [
    (httpx2.ReadTimeout("timed out"), httpx2.ReadTimeout),
    (httpx2.ConnectError("refused"), httpx2.ConnectError),
    (httpx2.RemoteProtocolError("closed"), httpx2.RemoteProtocolError),
])
def test_sdk_raises_transport_errors_unwrapped(monkeypatch, transport_error, expected_cls):
    """
    구현의 전제 확인: SDK 는 전송 계층 예외를 errors.APIError 등으로 감싸지 않고 그대로 올리며,
    구현의 분류(시간 초과 / 연결 오류)에 정확히 하나로 들어간다.
    """
    for name in ("GOOGLE_GENAI_HTTP_CLIENT", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_CLIENT_MODE"):
        monkeypatch.delenv(name, raising=False)
    server = FakeGeminiServer()
    server.respond(transport_error)
    sdk_client = _make_sdk_client(server)

    with pytest.raises(expected_cls) as exc_info:
        sdk_client.models.generate_content(model=tweet_summarizer.GEMINI_MODEL, contents="prompt",
                                           config=tweet_summarizer._gemini_request_config(1000))
    assert type(exc_info.value) is expected_cls
    is_timeout = isinstance(exc_info.value, tweet_summarizer.GEMINI_TIMEOUT_ERRORS)
    is_connection = isinstance(exc_info.value, tweet_summarizer.GEMINI_CONNECTION_ERRORS)
    assert is_timeout != is_connection
    assert is_timeout == (expected_cls is httpx2.ReadTimeout)
    assert len(server.requests) == 1


def test_sdk_api_error_exposes_retry_info_in_details(monkeypatch):
    """구현의 전제 확인: SDK 가 429 응답에 올리는 예외는 ClientError 이고, details 에 응답 본문 JSON 전체가 들어 있다."""
    for name in ("GOOGLE_GENAI_HTTP_CLIENT", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_CLIENT_MODE"):
        monkeypatch.delenv(name, raising=False)
    server = FakeGeminiServer()
    server.respond(_http(429, _quota_body("28s")))
    sdk_client = _make_sdk_client(server)

    with pytest.raises(errors.ClientError) as exc_info:
        sdk_client.models.generate_content(model=tweet_summarizer.GEMINI_MODEL, contents="prompt",
                                           config=tweet_summarizer._gemini_request_config(1000))
    assert exc_info.value.code == 429
    assert exc_info.value.details == _quota_body("28s")
    assert tweet_summarizer._get_server_retry_delay(exc_info.value) == 28.0


@pytest.mark.parametrize("failure", [
    _http(429, _quota_body("28s")),
    _http(400, _error_body(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")),
    _http(504, _error_body(504, "DEADLINE_EXCEEDED", "Deadline expired.")),
    httpx2.ConnectError("[Errno 111] Connection refused"),
    httpx2.ReadTimeout("The read operation timed out"),
], ids=["429", "400", "504", "connect_error", "read_timeout"])
def test_sdk_api_key_not_in_logs_or_result(sdk_gemini, caplog, failure):
    """오류 로그(예외 traceback 포함)와 반환값에 API 키가 섞여 나오지 않는다."""
    server, fake_time, sdk_sleep = sdk_gemini
    server.respond(failure)
    caplog.set_level("DEBUG")

    result = tweet_summarizer.call_gemini_api("prompt")

    # 키가 실제로 요청에 쓰였는지 먼저 확인 (검사가 의미 있도록)
    assert server.requests[0].headers["x-goog-api-key"] == DUMMY_API_KEY
    assert DUMMY_API_KEY not in result
    assert caplog.records, "오류 로그가 남아야 한다"
    assert DUMMY_API_KEY not in caplog.text
    for record in caplog.records:
        assert DUMMY_API_KEY not in record.getMessage()
        assert DUMMY_API_KEY not in (record.exc_text or "")


# ---------------------------------------------------------------------------
# 이어하기(재사용 + 사용자마다 저장) 회귀 테스트
#   - 실제 process_posts 를 쓰고 call_gemini_api 만 목 처리한다.
#   - GCS 는 업로드 시점의 로컬 파일 내용을 (날짜, 파일명) 키로 보관하는 메모리 기반 가짜로 대체한다.
#     (로컬 data/summarized_posts_agg.json 은 계속 덮어써지므로 업로드 시점의 내용을 잡아야 한다)
# ---------------------------------------------------------------------------
from collections import Counter  # noqa: E402

API_ERR = tweet_summarizer.API_ERROR_TEXT


def _long_text(tag):
    """250자 이상 원문 (번역 + 제목 + 요약 대상)"""
    return f"{tag} " + "word " * 60


def _mid_text(tag):
    """15자 이상 250자 미만 원문 (번역만 대상)"""
    return f"{tag} medium length post text"


def _make_post(tag, text, **extra):
    post = {"url": f"https://x.com/u/status/{tag}", "id": tag, "created_at": "2025-06-28T01:00:00.000Z", "text": text}
    post.update(extra)
    return post


def _kind_and_text(prompt):
    """프롬프트에서 (생성 필드, 원문) 을 구한다."""
    if prompt.startswith("Create a concise"):
        kind = 'title'
    elif prompt.startswith("Summarize"):
        kind = 'summary'
    else:
        assert prompt.startswith("Translate"), prompt[:40]
        kind = 'translated_text'
    text = prompt.split("\n---\n", 1)[1].rsplit("\n---", 1)[0]
    return kind, text


def _gen(kind, text):
    """가짜 Gemini 가 만드는 값 (필드·원문마다 고유)"""
    return f"{kind}<{text[:12]}>"


class FakeGcs:
    """(날짜, 파일명) -> JSON 문자열. 업로드 시점의 로컬 파일 내용을 보관하고, 다운로드하면 그 내용을 로컬에 쓴다."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.store = {}
        self.uploads = []    # (날짜, 파일명, 내용) 업로드 순서
        self.downloads = []  # (날짜, 파일명)

    def put(self, date_str, file_name, content):
        self.store[(date_str, file_name)] = json.dumps(content, ensure_ascii=False)

    def put_raw(self, date_str, file_name, raw):
        self.store[(date_str, file_name)] = raw

    def get(self, date_str, file_name):
        return json.loads(self.store[(date_str, file_name)])

    def download(self, file_name, date_str=None, **kwargs):
        self.downloads.append((date_str, file_name))
        if (date_str, file_name) not in self.store:
            return 1
        (self.data_dir / file_name).write_text(self.store[(date_str, file_name)], encoding='utf-8')
        return 0

    def upload(self, local_file_path, date_str=None, **kwargs):
        file_name = os.path.basename(local_file_path)
        with open(local_file_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        self.store[(date_str, file_name)] = raw
        self.uploads.append((date_str, file_name, json.loads(raw)))
        return 0

    def agg_uploads(self):
        return [content for _, name, content in self.uploads if name == 'summarized_posts_agg.json']


class FakeGemini:
    """call_gemini_api 대역. 호출을 (필드, 원문) 으로 기록한다. interrupt_on 원문을 만나면 KeyboardInterrupt 를 던진다."""

    def __init__(self):
        self.calls = []
        self.interrupt_on = None

    def __call__(self, prompt):
        kind, text = _kind_and_text(prompt)
        if self.interrupt_on is not None and text == self.interrupt_on:
            raise KeyboardInterrupt()
        self.calls.append((kind, text))
        return _gen(kind, text)


class ResumeEnv:
    def __init__(self, data_dir, gcs, gemini):
        self.data_dir = data_dir
        self.gcs = gcs
        self.gemini = gemini

    def write_user(self, user, posts):
        (self.data_dir / f"{user}_posts.json").write_text(json.dumps({"data": posts}, ensure_ascii=False),
                                                          encoding='utf-8')

    def local_json(self, file_name):
        return json.loads((self.data_dir / file_name).read_text(encoding='utf-8'))


@pytest.fixture
def resume_env(tmp_path, mocker):
    """임시 디렉토리 + 메모리 기반 가짜 GCS + 가짜 Gemini. 실제 process_posts 를 쓴다."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(tweet_summarizer, 'pjt_home_path', str(tmp_path))
    gcs = FakeGcs(data_dir)
    gemini = FakeGemini()
    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', side_effect=gcs.download)
    mocker.patch.object(tweet_summarizer.gcs_upload_json, 'upload_local_file_to_gcs', side_effect=gcs.upload)
    mocker.patch.object(tweet_summarizer, 'call_gemini_api', side_effect=gemini)
    return ResumeEnv(data_dir, gcs, gemini)


def _expected_long(post):
    """250자 이상 게시물이 요약된 모습"""
    expected = dict(post)
    expected['translated_text'] = _gen('translated_text', post['text'])
    expected['title'] = _gen('title', post['text'])
    expected['summary'] = _gen('summary', post['text'])
    return expected


def _expected_mid(post):
    expected = dict(post)
    expected['translated_text'] = _gen('translated_text', post['text'])
    return expected


def _summarized_long(tag, **override):
    """이미 요약된 250자 이상 게시물 (누적 파일에 들어 있는 모습)"""
    post = _expected_long(_make_post(tag, _long_text(tag)))
    post.update(override)
    return post


def _urls(posts):
    return [p['url'] for p in posts]


# --- 중단 후 이어하기 ---

def _setup_two_users(env):
    users = {
        'u1': [_make_post('1a', _long_text('1a')), _make_post('1b', _mid_text('1b'))],
        'u2': [_make_post('2a', _long_text('2a')), _make_post('2b', _mid_text('2b'))],
    }
    for user, posts in users.items():
        env.write_user(user, posts)
    return users


def test_interrupt_in_second_user_keeps_first_user_in_gcs_and_resume_calls_only_remaining(resume_env):
    """
    두 번째 사용자 처리 중 BaseException(KeyboardInterrupt)으로 끊겨도 GCS 누적에 첫 번째 사용자의 결과가 남고,
    다시 실행하면 남은 게시물에 대해서만 Gemini 를 부르며, 같은 입력으로 한 번 더 실행하면 호출이 0회이다.
    """
    env = resume_env
    users = _setup_two_users(env)
    env.gemini.interrupt_on = users['u2'][1]['text']  # u2 의 두 번째 게시물을 처리하는 도중 끊김

    with pytest.raises(KeyboardInterrupt):
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])

    # 끊긴 시점의 GCS 누적 = 첫 번째 사용자의 결과 (업로드 시점 내용 기준)
    agg_in_gcs = env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')
    assert agg_in_gcs == [_expected_long(users['u1'][0]), _expected_mid(users['u1'][1])]
    assert (BASE_YMD, 'summarized_posts.json') not in env.gcs.store, "끊겼으므로 최종 파일은 올라가면 안 된다"

    # 다시 실행 -> 끊기기 전 완료한 사용자(u1)는 호출하지 않는다.
    # (처리 도중 끊긴 u2 는 저장 단위가 사용자이므로 u2 의 게시물을 처음부터 다시 처리한다)
    env.gemini.interrupt_on = None
    env.gemini.calls.clear()
    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])

    assert env.gemini.calls == [
        ('translated_text', users['u2'][0]['text']), ('title', users['u2'][0]['text']),
        ('summary', users['u2'][0]['text']), ('translated_text', users['u2'][1]['text'])]
    resumed_agg = env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')
    assert resumed_agg == [_expected_long(users['u1'][0]), _expected_mid(users['u1'][1]),
                           _expected_long(users['u2'][0]), _expected_mid(users['u2'][1])]
    assert _urls(env.gcs.get(BASE_YMD, 'summarized_posts.json')) == _urls(resumed_agg)

    # 같은 입력으로 한 번 더 -> Gemini 호출 0회, 결과 동일
    env.gemini.calls.clear()
    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])
    assert env.gemini.calls == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == resumed_agg


def test_resumed_result_equals_uninterrupted_result(resume_env):
    """중단했다가 이어서 한 최종 누적 파일은 한 번에 끝까지 한 결과와 구성·순서가 같다."""
    env = resume_env
    users = _setup_two_users(env)

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])
    uninterrupted_agg = env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')
    uninterrupted_posts = env.gcs.get(BASE_YMD, 'summarized_posts.json')

    env.gcs.store.clear()
    env.gemini.interrupt_on = users['u2'][0]['text']
    with pytest.raises(KeyboardInterrupt):
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])
    env.gemini.interrupt_on = None
    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])

    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == uninterrupted_agg
    assert env.gcs.get(BASE_YMD, 'summarized_posts.json') == uninterrupted_posts


def test_agg_saved_after_each_user_with_processed_posts_only(resume_env):
    """
    누적 파일은 처리한 게시물이 1건 이상인 사용자마다 올라간다. 수집 파일이 없거나 게시물이 0건인 사용자는 올리지 않고,
    각 업로드 내용은 (지금까지 처리한 게시물 + 시작할 때 받은 당일 누적) 이며 summarized_posts.json 보다 먼저 올라간다.
    """
    env = resume_env
    old_post = _summarized_long('old')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [old_post])
    p1 = _make_post('1a', _long_text('1a'))
    p3 = _make_post('3a', _mid_text('3a'))
    env.write_user('u1', [p1])
    env.write_user('u_empty', [])
    # u_missing 은 수집 파일 없음
    env.write_user('u3', [p3])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u_empty', 'u_missing', 'u3'])

    aggs = env.gcs.agg_uploads()
    assert aggs == [[_expected_long(p1), old_post],
                    [_expected_long(p1), _expected_mid(p3), old_post]]
    names = [(date_str, name) for date_str, name, _ in env.gcs.uploads]
    # summarized_posts.json 은 태그 전·후로 두 번 올라가지만, 누적 파일 업로드는 모두 그보다 먼저다.
    assert names == [(BASE_YMD, 'summarized_posts_agg.json')] * 2 + [(BASE_YMD, 'summarized_posts.json')] * 2


def test_no_agg_upload_when_no_user_has_posts(resume_env):
    """처리한 게시물이 하나도 없으면 누적 파일을 올리지 않는다 (기존 누적을 빈 목록으로 덮어쓰지 않는다)."""
    env = resume_env
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [_summarized_long('old')])
    env.write_user('u_empty', [])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u_empty', 'u_missing'])

    assert env.gcs.agg_uploads() == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [_summarized_long('old')]


def test_agg_is_today_agg_even_though_prev_agg_downloaded_to_same_local_path(resume_env):
    """전일 누적 파일을 받아 로컬 파일이 전일 내용이어도, 당일 경로로 올라가는 누적은 당일 누적(+이번 처리분)이다."""
    env = resume_env
    today_old = _summarized_long('today_old')
    prev_only = _summarized_long('prev_only')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [prev_only])
    p1 = _make_post('1a', _mid_text('1a'))
    env.write_user('u1', [p1])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [_expected_mid(p1), today_old]
    assert env.gcs.get(PREV_YMD, 'summarized_posts_agg.json') == [prev_only], "전일 파일은 건드리지 않는다"
    assert all(date_str == BASE_YMD for date_str, _, _ in env.gcs.uploads)


def test_final_agg_order_and_dedup_matches_old_logic(resume_env):
    """최종 누적 = 이번 처리분(사용자 순서) + 기존 당일 누적, url 중복은 앞쪽(이번 처리분) 유지, url 없는 항목은 그대로 유지."""
    env = resume_env
    old_same_url = _summarized_long('1a', title="옛 제목")
    old_other = _summarized_long('old')
    no_url_old = {"id": "nourl", "text": "x"}
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [old_same_url, no_url_old, old_other])
    # 원문이 달라진 같은 url -> 재요약되어 이번 결과가 남는다
    p1 = _make_post('1a', _long_text('1a') + " edited")
    p2 = _make_post('2a', _mid_text('2a'))
    env.write_user('u1', [p1])
    env.write_user('u2', [p2])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])

    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [
        _expected_long(p1), _expected_mid(p2), no_url_old, old_other]


@pytest.mark.parametrize("raw", ["{not json", '{"a": 1}', '[1, 2]', '"text"'],
                         ids=["json_error", "dict", "list_of_non_dict", "str"])
def test_broken_today_agg_aborts_without_overwriting(resume_env, raw):
    """당일 누적 파일이 깨졌으면(JSON 오류, dict 의 list 가 아님) 아무것도 올리지 않고 SystemExit 한다."""
    env = resume_env
    env.gcs.put_raw(BASE_YMD, 'summarized_posts_agg.json', raw)
    env.write_user('u1', [_make_post('1a', _long_text('1a'))])

    with pytest.raises(SystemExit) as exc_info:
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert exc_info.value.code == 1
    assert env.gcs.uploads == []
    assert env.gcs.store[(BASE_YMD, 'summarized_posts_agg.json')] == raw
    assert env.gemini.calls == [], "누적 파일 확인 전에는 요약을 시작하면 안 된다"


def test_gcs_mode_false_uses_no_gcs_and_writes_only_summarized_posts(resume_env):
    """gcs_mode=False 이면 GCS 다운로드·업로드를 하지 않고 summarized_posts.json 만 쓴다 (재사용도 없다)."""
    env = resume_env
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [_summarized_long('1a')])  # 있어도 쓰지 않는다
    p1 = _make_post('1a', _long_text('1a'))
    env.write_user('u1', [p1])

    tweet_summarizer.main(BASE_YMD, gcs_mode=False, tweet_usernames=['u1'])

    assert env.gcs.downloads == []
    assert env.gcs.uploads == []
    assert env.local_json('summarized_posts.json') == [_expected_long(p1)]
    assert not (env.data_dir / 'summarized_posts_agg.json').exists()
    assert len(env.gemini.calls) == 3


# --- [중복] 태그 ---

def test_dup_tag_only_in_summarized_posts_not_in_agg_and_not_nested_on_reruns(resume_env, caplog):
    """
    전일 누적에만 있던 게시물을 재사용하면 summarized_posts.json 에는 [중복] 태그가 붙고(title 이 있으면 title,
    없으면 translated_text) 누적 파일에는 붙지 않는다. 여러 번 실행해도 태그가 중첩되지 않는다.
    """
    env = resume_env
    prev_long = _summarized_long('1a')
    prev_mid = _expected_mid(_make_post('1b', _mid_text('1b')))
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [prev_long, prev_mid])
    new_post = _make_post('1c', _mid_text('1c'))
    env.write_user('u1', [_make_post('1a', _long_text('1a')), _make_post('1b', _mid_text('1b')), new_post])

    for run in range(3):
        env.gemini.calls.clear()
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

        # 재사용한 게시물은 Gemini 를 부르지 않고, 새 게시물 1건(번역 1회)만 첫 실행에서 처리한다 (이후엔 당일 누적에서 재사용)
        assert env.gemini.calls == ([('translated_text', new_post['text'])] if run == 0 else [])

        posts = {p['url']: p for p in env.gcs.get(BASE_YMD, 'summarized_posts.json')}
        assert posts[prev_long['url']]['title'] == f"[중복] {prev_long['title']}"
        assert posts[prev_long['url']]['translated_text'] == prev_long['translated_text']
        assert posts[prev_mid['url']]['translated_text'] == f"[중복] {prev_mid['translated_text']}"
        # 전일 누적에 없는 게시물에는 태그가 없다 (당일 누적에서 재사용한 두 번째 실행 이후에도)
        assert posts[new_post['url']]['translated_text'] == _gen('translated_text', new_post['text'])
        # 로컬 파일도 GCS 에 올라간 것과 같다
        assert env.local_json('summarized_posts.json') == env.gcs.get(BASE_YMD, 'summarized_posts.json')

        agg_text = env.gcs.store[(BASE_YMD, 'summarized_posts_agg.json')]
        assert "[중복]" not in agg_text, f"누적 파일에 태그가 들어갔다 (실행 {run + 1}회차)"
        assert "[중복] [중복]" not in env.gcs.store[(BASE_YMD, 'summarized_posts.json')]

    assert env.gcs.get(PREV_YMD, 'summarized_posts_agg.json') == [prev_long, prev_mid], "전일 파일은 건드리지 않는다"


def test_no_dup_tag_and_warning_when_prev_agg_missing_even_if_today_agg_exists(resume_env, caplog):
    """전일 누적이 없으면 당일 누적에 있는 게시물이라도 태그를 붙이지 않고, 경고 로그만 남긴다."""
    env = resume_env
    today_post = _summarized_long('1a')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_post])
    env.write_user('u1', [_make_post('1a', _long_text('1a'))])
    caplog.set_level("INFO")

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gemini.calls == []
    posts = env.gcs.get(BASE_YMD, 'summarized_posts.json')
    assert posts[0]['title'] == today_post['title']
    assert any(r.levelname == "WARNING" and "전일자 중복 체크를 건너뜁니다" in r.getMessage() for r in caplog.records)


# --- 재사용 조건 (main 경유) ---

def test_today_agg_takes_priority_over_prev_agg_for_same_url(resume_env):
    """같은 URL 이 당일·전일 누적에 모두 있으면 당일 것을 재사용한다."""
    env = resume_env
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [_summarized_long('1a', title="당일 제목")])
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [_summarized_long('1a', title="전일 제목")])
    env.write_user('u1', [_make_post('1a', _long_text('1a'))])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gemini.calls == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')[0]['title'] == "당일 제목"


def test_prev_agg_used_when_today_agg_has_no_such_url(resume_env):
    env = resume_env
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [_summarized_long('other')])
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [_summarized_long('1a', title="전일 제목")])
    env.write_user('u1', [_make_post('1a', _long_text('1a'))])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gemini.calls == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')[0]['title'] == "전일 제목"


def test_same_url_in_later_user_reuses_result_within_same_run(resume_env):
    """같은 실행 안에서 같은 URL 이 다른 사용자 파일에 다시 나오면 앞에서 처리한 결과를 재사용한다."""
    env = resume_env
    shared = _make_post('9a', _long_text('9a'))
    env.write_user('u1', [shared])
    env.write_user('u2', [dict(shared)])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1', 'u2'])

    assert len(env.gemini.calls) == 3  # u1 에서만 호출
    agg = env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')
    assert agg == [_expected_long(shared)], "누적에는 url 당 1건"
    assert env.gcs.get(BASE_YMD, 'summarized_posts.json') == [_expected_long(shared), _expected_long(shared)]


def test_summary_stats_log(resume_env, caplog):
    """실행 끝에 처리 게시물·Gemini 호출·재사용 게시물·일부 필드만 재사용 집계를 항목별 INFO 로그 4개로 남긴다."""
    env = resume_env
    # 1a: 전부 재사용, 1b: title 만 API 오류 -> 일부 재사용(호출 1회), 1c: 신규 짧은 글(호출 1회), 1d: 15자 미만(호출 0회)
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json',
                [_summarized_long('1a'), _summarized_long('1b', title=API_ERR)])
    env.write_user('u1', [_make_post('1a', _long_text('1a')), _make_post('1b', _long_text('1b')),
                          _make_post('1c', _mid_text('1c')), _make_post('1d', "tiny")])
    caplog.set_level("INFO")

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    records = [r for r in caplog.records if "Tweet 요약 집계" in r.getMessage()]
    assert [r.getMessage() for r in records] == [
        "Tweet 요약 집계 - 처리 게시물: 4건",
        "Tweet 요약 집계 - Gemini 호출: 2회",
        "Tweet 요약 집계 - 재사용 게시물: 1건",
        "Tweet 요약 집계 - 일부 필드만 재사용: 1건",
    ]
    assert all(r.levelname == "INFO" for r in records)


# --- 재사용 조건 경계 (process_posts 직접 호출) ---

@pytest.fixture
def pp_env(tmp_path, mocker):
    """process_posts 를 직접 부르기 위한 환경: 가짜 Gemini + 수집 파일 작성 도우미"""
    gemini = FakeGemini()
    mocker.patch.object(tweet_summarizer, 'call_gemini_api', side_effect=gemini)

    def run(posts, reuse_posts):
        input_filename = tmp_path / "x_posts.json"
        input_filename.write_text(json.dumps({"data": posts}, ensure_ascii=False), encoding='utf-8')
        out, stats = [], Counter()
        tweet_summarizer.process_posts(str(input_filename), out, reuse_posts, stats)
        return out, stats

    return gemini, run


def test_process_posts_reuses_all_fields_and_keeps_new_metadata(pp_env):
    """원문이 같으면 필요한 필드를 모두 재사용한다. 수집 파일의 다른 값(좋아요 수 등)은 이번 수집 값을 쓴다."""
    gemini, run = pp_env
    cached = _summarized_long('1a', likes=1)
    new_post = _make_post('1a', _long_text('1a'), likes=99)

    out, stats = run([new_post], {cached['url']: cached})

    assert gemini.calls == []
    assert out == [dict(new_post, translated_text=cached['translated_text'], title=cached['title'],
                        summary=cached['summary'])]
    assert out[0]['likes'] == 99
    assert stats['reused_posts'] == 1 and stats['partially_reused_posts'] == 0 and stats['gemini_calls'] == 0


def test_process_posts_text_changed_resummarizes_everything(pp_env):
    """같은 URL 이라도 원문이 다르면 모든 필드를 다시 요약한다."""
    gemini, run = pp_env
    cached = _summarized_long('1a')
    new_post = _make_post('1a', _long_text('1a') + " edited")

    out, stats = run([new_post], {cached['url']: cached})

    assert [kind for kind, _ in gemini.calls] == ['translated_text', 'title', 'summary']
    assert out == [_expected_long(new_post)]
    assert stats['gemini_calls'] == 3 and stats['reused_posts'] == 0 and stats['partially_reused_posts'] == 0


@pytest.mark.parametrize("bad_field", ['translated_text', 'title', 'summary'])
@pytest.mark.parametrize("bad_value", [API_ERR, "", "   ", None, 123, ['x']],
                         ids=["api_error", "empty", "blank", "none", "int", "list"])
def test_process_posts_resummarizes_only_the_unusable_field(pp_env, bad_field, bad_value):
    """필드 하나가 API_ERROR_TEXT/빈 문자열/문자열 아님이면 그 필드만 다시 호출하고 나머지는 재사용한다."""
    gemini, run = pp_env
    cached = _summarized_long('1a')
    cached[bad_field] = bad_value
    new_post = _make_post('1a', _long_text('1a'))

    out, stats = run([new_post], {cached['url']: cached})

    assert gemini.calls == [(bad_field, new_post['text'])]
    assert out == [_expected_long(new_post) | {k: cached[k] for k in ('translated_text', 'title', 'summary')
                                               if k != bad_field}]
    assert out[0][bad_field] == _gen(bad_field, new_post['text'])
    assert stats['gemini_calls'] == 1 and stats['partially_reused_posts'] == 1 and stats['reused_posts'] == 0


@pytest.mark.parametrize("missing", [['title'], ['summary'], ['title', 'summary'], ['translated_text', 'title', 'summary']])
def test_process_posts_missing_fields_are_generated(pp_env, missing):
    """후보에 필드가 아예 없으면 그 필드만 호출한다 (예: 이전 규칙으로 번역만 만든 같은 원문)."""
    gemini, run = pp_env
    cached = _summarized_long('1a')
    for field in missing:
        del cached[field]
    new_post = _make_post('1a', _long_text('1a'))

    out, stats = run([new_post], {cached['url']: cached})

    assert [kind for kind, _ in gemini.calls] == missing
    assert out == [_expected_long(new_post) | {k: cached[k] for k in ('translated_text', 'title', 'summary')
                                               if k in cached}]
    if len(missing) == 3:
        assert stats['reused_posts'] == 0 and stats['partially_reused_posts'] == 0
    else:
        assert stats['partially_reused_posts'] == 1


def test_process_posts_length_class_change_resummarizes(pp_env):
    """이전에는 15자 미만이라 번역이 없었는데 지금은 긴 글이면(원문이 다르므로) 전부 새로 요약한다."""
    gemini, run = pp_env
    cached = _make_post('1a', "tiny")  # 15자 미만: 번역 없음
    new_post = _make_post('1a', _long_text('1a'))

    out, stats = run([new_post], {cached['url']: cached})

    assert [kind for kind, _ in gemini.calls] == ['translated_text', 'title', 'summary']
    assert out == [_expected_long(new_post)]


def test_process_posts_short_post_needs_only_translated_text(pp_env):
    """15자 이상 250자 미만 글은 translated_text 만 필요하다. 후보에 title/summary 가 있어도 쓰지 않는다."""
    gemini, run = pp_env
    cached = _expected_mid(_make_post('1a', _mid_text('1a')))
    cached['title'] = "필요 없는 제목"
    new_post = _make_post('1a', _mid_text('1a'))

    out, stats = run([new_post], {cached['url']: cached})

    assert gemini.calls == []
    assert out == [dict(new_post, translated_text=cached['translated_text'])]
    assert 'title' not in out[0] and 'summary' not in out[0]
    assert stats['reused_posts'] == 1


@pytest.mark.parametrize("text, expected_calls", [("a" * 14, 0), ("a" * 15, 1), ("a" * 249, 1), ("a" * 250, 3)])
def test_process_posts_length_thresholds_without_candidate(pp_env, text, expected_calls):
    """길이 구간 경계: 15자 미만 호출 없음, 15~249자 번역 1회, 250자 이상 3회."""
    gemini, run = pp_env

    out, stats = run([_make_post('1a', text)], {})

    assert len(gemini.calls) == expected_calls
    assert len(out) == 1
    assert stats['gemini_calls'] == expected_calls


def test_process_posts_tiny_post_with_candidate_is_not_counted_as_reused(pp_env):
    """15자 미만 글은 만들 필드가 없으므로 호출도 재사용도 없다."""
    gemini, run = pp_env
    cached = _make_post('1a', "tiny", translated_text="번역")
    new_post = _make_post('1a', "tiny")

    out, stats = run([new_post], {cached['url']: cached})

    assert gemini.calls == []
    assert out == [new_post]
    assert stats['reused_posts'] == 0 and stats['partially_reused_posts'] == 0


def test_process_posts_empty_text_is_appended_without_calls(pp_env):
    """원문이 빈 게시물은 호출 없이 그대로 추가하고, 후보가 있어도 값을 채우지 않는다."""
    gemini, run = pp_env
    cached = _summarized_long('1a')
    empty_post = _make_post('1a', "")

    out, stats = run([empty_post], {cached['url']: cached})

    assert gemini.calls == []
    assert out == [empty_post]
    assert stats == Counter()


def test_process_posts_post_without_url_is_never_reused(pp_env):
    """URL 이 없는 게시물은 재사용 후보가 되지 않고 재사용 맵에도 들어가지 않는다 (url 없는 같은 원문 2건 모두 호출)."""
    gemini, run = pp_env
    reuse = {"": _summarized_long('x'), None: _summarized_long('y')}  # 잘못된 키가 있어도 url 없는 게시물이 끌려오지 않는다
    no_url_a = {"id": "n1", "text": _mid_text('n')}
    no_url_b = {"id": "n2", "text": _mid_text('n')}
    empty_url = {"id": "n3", "url": "", "text": _mid_text('n')}
    size_before = len(reuse)

    out, stats = run([no_url_a, no_url_b, empty_url], reuse)

    assert len(gemini.calls) == 3
    assert len(reuse) == size_before
    assert stats['reused_posts'] == 0


def test_process_posts_registers_processed_post_for_later_reuse(pp_env):
    """처리한 게시물은 재사용 맵에 들어가, 같은 파일·다음 파일에서 같은 URL 이 나오면 호출하지 않는다."""
    gemini, run = pp_env
    reuse = {}
    first = _make_post('1a', _long_text('1a'))

    out1, _ = run([first, dict(first)], reuse)
    assert len(gemini.calls) == 3
    assert out1[0] == out1[1] == _expected_long(first)
    assert reuse[first['url']]['title'] == _gen('title', first['text'])

    gemini.calls.clear()
    out2, stats2 = run([dict(first)], reuse)
    assert gemini.calls == []
    assert stats2['reused_posts'] == 1


def test_process_posts_without_reuse_map_always_calls(pp_env):
    """reuse_posts 를 주지 않으면(None) 재사용할 후보가 없으므로 서로 다른 URL 게시물은 모두 호출한다."""
    gemini, run = pp_env
    posts = [_make_post('1a', _mid_text('1a')), _make_post('1b', _mid_text('1b'))]

    out, stats = run(posts, None)

    assert len(gemini.calls) == 2
    assert out == [_expected_mid(p) for p in posts]
    assert stats['reused_posts'] == 0


def test_build_reuse_posts_priority_and_missing_urls():
    """당일이 전일보다, 한 목록 안에서는 앞쪽이 우선하며 url 이 없는(빈) 항목은 넣지 않는다."""
    today = [{"url": "u1", "v": "today-first"}, {"url": "u1", "v": "today-second"}, {"v": "no-url"}, {"url": "", "v": "empty"}]
    prev = [{"url": "u1", "v": "prev"}, {"url": "u2", "v": "prev2"}]

    reuse = tweet_summarizer.build_reuse_posts(today, prev)

    assert reuse == {"u1": {"url": "u1", "v": "today-first"}, "u2": {"url": "u2", "v": "prev2"}}
    assert tweet_summarizer.build_reuse_posts([], []) == {}


@pytest.mark.parametrize("candidate, field, expected", [
    (None, 'title', None),
    ({}, 'title', None),
    ({"title": "제목"}, 'title', "제목"),
    ({"title": API_ERR}, 'title', None),
    ({"title": ""}, 'title', None),
    ({"title": " \n"}, 'title', None),
    ({"title": None}, 'title', None),
    ({"title": 5}, 'title', None),
    ({"title": "제목"}, 'summary', None),
])
def test_get_reusable_value(candidate, field, expected):
    assert tweet_summarizer._get_reusable_value(candidate, field) == expected


# --- 전일·당일 누적 파일 다운로드 실패·오류 처리 ---

def _override_download(env, mocker, overrides):
    """
    FakeGcs.download 를 감싼다. overrides[date_str] 가 int 이면 다운로드 없이 그 값을 돌려주고(로컬 파일은 건드리지 않음),
    예외 인스턴스이면 그 예외를 던진다. overrides 에 없는 날짜는 FakeGcs 가 처리한다.
    """
    def download(file_name, date_str=None, **kwargs):
        if date_str in overrides:
            env.gcs.downloads.append((date_str, file_name))
            action = overrides[date_str]
            if isinstance(action, BaseException):
                raise action
            return action
        return env.gcs.download(file_name, date_str=date_str, **kwargs)

    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', side_effect=download)


def _prev_skip_warning(caplog):
    return [r for r in caplog.records
            if r.levelname == "WARNING" and "전일자 중복 체크를 건너뜁니다" in r.getMessage()]


def _prev_error_logs(caplog):
    return [r for r in caplog.records if r.levelname == "ERROR" and PREV_YMD in r.getMessage()]


_BROKEN_PREV_RAWS = [
    ("{not json", "json_error"),
    ('{"a": 1}', "dict"),
    ('[1, 2]', "list_of_non_dict"),
    ('"text"', "str"),
    (json.dumps([_summarized_long('1a'), 5]), "list_with_valid_dict_and_non_dict"),
]


@pytest.mark.parametrize("raw", [r for r, _ in _BROKEN_PREV_RAWS], ids=[i for _, i in _BROKEN_PREV_RAWS])
def test_broken_prev_agg_is_treated_as_missing_and_run_continues(resume_env, caplog, raw):
    """
    전일 누적 파일이 깨졌으면(JSON 오류, dict, dict 가 아닌 항목의 list, 문자열) 중단하지 않고 없는 것으로 본다.
    ERROR 로그(전일 날짜 포함)가 남고, 전일 것은 재사용·[중복] 태그에 쓰이지 않으며(일부만 유효한 목록도 마찬가지),
    당일 누적 재사용·저장과 summarized_posts.json 업로드는 정상이고 GCS 의 전일 파일은 그대로다.
    """
    env = resume_env
    today_old = _summarized_long('1a', title="당일 제목")
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    env.gcs.put_raw(PREV_YMD, 'summarized_posts_agg.json', raw)
    # 1a: 당일 누적에서 재사용 (전일 raw 의 일부 유효 항목이 있어도 [중복] 태그가 붙으면 안 됨), 1b: 신규 (번역 1회)
    p1a = _make_post('1a', _long_text('1a'))
    p1b = _make_post('1b', _mid_text('1b'))
    env.write_user('u1', [p1a, p1b])
    caplog.set_level("INFO")

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gemini.calls == [('translated_text', p1b['text'])]
    errors_ = _prev_error_logs(caplog)
    assert len(errors_) == 1
    assert len(_prev_skip_warning(caplog)) == 1

    expected_posts = [today_old, _expected_mid(p1b)]
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == expected_posts
    final_posts = env.gcs.get(BASE_YMD, 'summarized_posts.json')
    assert final_posts == expected_posts
    assert "[중복]" not in env.gcs.store[(BASE_YMD, 'summarized_posts.json')]
    assert env.local_json('summarized_posts.json') == final_posts
    # 업로드는 모두 당일 경로이고, 깨진 전일 내용이 당일 누적으로 올라가지 않았다
    assert env.gcs.uploads and all(date_str == BASE_YMD for date_str, _, _ in env.gcs.uploads)
    assert all(content == expected_posts for content in env.gcs.agg_uploads())
    assert env.gcs.store[(PREV_YMD, 'summarized_posts_agg.json')] == raw
    # 로컬 누적 파일도 전일 내용이 아니라 당일 누적이다
    assert env.local_json('summarized_posts_agg.json') == env.gcs.get(BASE_YMD, 'summarized_posts_agg.json')


@pytest.mark.parametrize("raw", [r for r, _ in _BROKEN_PREV_RAWS], ids=[i for _, i in _BROKEN_PREV_RAWS])
def test_broken_prev_agg_result_equals_missing_prev_agg_result(resume_env, raw):
    """전일 누적이 깨졌을 때의 GCS 결과(누적·summarized_posts.json)와 Gemini 호출은 전일 누적이 없을 때와 같다."""
    env = resume_env
    today_old = _summarized_long('old')
    p1a = _make_post('1a', _long_text('1a'))
    p1b = _make_post('1b', _mid_text('1b'))

    def run(prev_raw):
        env.gcs.store.clear()
        env.gcs.uploads.clear()
        env.gemini.calls.clear()
        env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
        if prev_raw is not None:
            env.gcs.put_raw(PREV_YMD, 'summarized_posts_agg.json', prev_raw)
        env.write_user('u1', [p1a, p1b])
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])
        return (list(env.gemini.calls), env.gcs.get(BASE_YMD, 'summarized_posts_agg.json'),
                env.gcs.get(BASE_YMD, 'summarized_posts.json'), [(d, n) for d, n, _ in env.gcs.uploads])

    missing_result = run(None)
    broken_result = run(raw)

    assert broken_result == missing_result


def test_broken_prev_agg_with_today_agg_missing_still_runs(resume_env, caplog):
    """당일 누적이 없고(ret 1) 전일 누적이 깨져 있어도 빈 누적으로 시작해 모든 게시물을 요약하고 올린다."""
    env = resume_env
    env.gcs.put_raw(PREV_YMD, 'summarized_posts_agg.json', "{not json")
    p1a = _make_post('1a', _long_text('1a'))
    env.write_user('u1', [p1a])
    caplog.set_level("INFO")

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert len(env.gemini.calls) == 3
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [_expected_long(p1a)]
    assert env.gcs.get(BASE_YMD, 'summarized_posts.json') == [_expected_long(p1a)]
    assert len(_prev_error_logs(caplog)) == 1
    assert len(_prev_skip_warning(caplog)) == 1


def test_broken_prev_agg_not_uploaded_when_no_user_has_posts(resume_env):
    """전일 누적이 깨졌고 처리한 게시물도 없으면 누적 파일은 올라가지 않고 GCS 의 당일 누적은 그대로다 (summarized_posts.json 은 빈 목록)."""
    env = resume_env
    today_old = _summarized_long('old')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    env.gcs.put_raw(PREV_YMD, 'summarized_posts_agg.json', "{not json")
    env.write_user('u_empty', [])

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u_empty'])

    assert env.gcs.agg_uploads() == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [today_old]
    assert env.gcs.get(BASE_YMD, 'summarized_posts.json') == []
    assert env.gcs.store[(PREV_YMD, 'summarized_posts_agg.json')] == "{not json"


def test_prev_agg_with_undecodable_bytes_is_treated_as_missing(resume_env, mocker, caplog):
    """UTF-8 로 읽을 수 없는 전일 누적(UnicodeDecodeError, ValueError 계열)도 없는 것으로 보고 계속 진행한다."""
    env = resume_env

    def download(file_name, date_str=None, **kwargs):
        env.gcs.downloads.append((date_str, file_name))
        if date_str == PREV_YMD:
            (env.data_dir / file_name).write_bytes(b'\xff\xfe\x00bad')
            return 0
        return 1

    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', side_effect=download)
    p1a = _make_post('1a', _mid_text('1a'))
    env.write_user('u1', [p1a])
    caplog.set_level("INFO")

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gcs.get(BASE_YMD, 'summarized_posts.json') == [_expected_mid(p1a)]
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [_expected_mid(p1a)]
    assert len(_prev_error_logs(caplog)) == 1


@pytest.mark.parametrize("ret", [1, 2])
def test_prev_agg_download_nonzero_ret_is_treated_as_missing(resume_env, mocker, caplog, ret):
    """전일 누적 다운로드 반환값이 0 이 아니면(1: 없음, 2: 접근 오류) 없는 것으로 보고 태그 없이 계속 진행하며 경고를 남긴다."""
    env = resume_env
    today_old = _summarized_long('1a')
    # 전일 파일이 GCS 에 있어도 (ret=2 처럼) 받지 못했다면 쓰지 않는다
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [_summarized_long('1a', title="전일 제목")])
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    _override_download(env, mocker, {PREV_YMD: ret})
    p1a = _make_post('1a', _long_text('1a'))
    env.write_user('u1', [p1a])
    caplog.set_level("INFO")

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gemini.calls == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts.json') == [today_old]
    assert "[중복]" not in env.gcs.store[(BASE_YMD, 'summarized_posts.json')]
    assert len(_prev_skip_warning(caplog)) == 1
    assert _prev_error_logs(caplog) == []


@pytest.mark.parametrize("exc", [ConnectionError("network down"), OSError("disk"), RuntimeError("boom")],
                         ids=["ConnectionError", "OSError", "RuntimeError"])
def test_prev_agg_download_exception_aborts(resume_env, mocker, exc):
    """전일 누적 다운로드 중 ValueError 가 아닌 예외가 나면 SystemExit(1) 로 중단한다 (업로드 없음, 당일 누적 그대로)."""
    env = resume_env
    today_old = _summarized_long('old')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    _override_download(env, mocker, {PREV_YMD: exc})
    env.write_user('u1', [_make_post('1a', _long_text('1a'))])

    with pytest.raises(SystemExit) as exc_info:
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert exc_info.value.code == 1
    assert env.gcs.uploads == []
    assert env.gemini.calls == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [today_old]


@pytest.mark.parametrize("ret", [2, 3, -1])
def test_today_agg_download_ret_not_0_or_1_aborts_before_summarizing(resume_env, mocker, ret):
    """
    당일 누적 다운로드 반환값이 0 도 1 도 아니면(예: 2) 요약을 시작하기 전에 SystemExit(1) 로 중단한다.
    Gemini 호출·업로드 0회이고 GCS 의 기존 당일 누적은 그대로다.
    """
    env = resume_env
    today_old = _summarized_long('old')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [_summarized_long('prev')])
    _override_download(env, mocker, {BASE_YMD: ret})
    env.write_user('u1', [_make_post('1a', _long_text('1a')), _make_post('1b', _mid_text('1b'))])

    with pytest.raises(SystemExit) as exc_info:
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert exc_info.value.code == 1
    assert env.gemini.calls == []
    assert env.gcs.uploads == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [today_old]
    assert (BASE_YMD, 'summarized_posts.json') not in env.gcs.store


def test_today_agg_download_exception_aborts(resume_env, mocker):
    """당일 누적 다운로드 자체가 예외로 실패해도 SystemExit(1) 로 중단하고 아무것도 올리지 않는다."""
    env = resume_env
    today_old = _summarized_long('old')
    env.gcs.put(BASE_YMD, 'summarized_posts_agg.json', [today_old])
    _override_download(env, mocker, {BASE_YMD: ConnectionError("network down")})
    env.write_user('u1', [_make_post('1a', _long_text('1a'))])

    with pytest.raises(SystemExit) as exc_info:
        tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert exc_info.value.code == 1
    assert env.gcs.uploads == []
    assert env.gemini.calls == []
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [today_old]


def test_today_agg_ret_1_starts_empty_and_runs_normally(resume_env):
    """당일 누적 파일이 없으면(반환값 1) 빈 누적으로 시작해 정상 진행한다. 전일 누적은 재사용·태그에 쓰인다."""
    env = resume_env
    prev_post = _summarized_long('1a')
    env.gcs.put(PREV_YMD, 'summarized_posts_agg.json', [prev_post])
    p1a = _make_post('1a', _long_text('1a'))
    p1b = _make_post('1b', _mid_text('1b'))
    env.write_user('u1', [p1a, p1b])
    assert (BASE_YMD, 'summarized_posts_agg.json') not in env.gcs.store

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['u1'])

    assert env.gemini.calls == [('translated_text', p1b['text'])]
    assert env.gcs.get(BASE_YMD, 'summarized_posts_agg.json') == [prev_post, _expected_mid(p1b)]
    final_posts = env.gcs.get(BASE_YMD, 'summarized_posts.json')
    assert final_posts[0]['title'] == f"[중복] {prev_post['title']}"
    assert final_posts[1] == _expected_mid(p1b)
