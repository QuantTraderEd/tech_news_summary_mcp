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
