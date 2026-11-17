import os
import site
import json
import time

import httpx2
import pytest
import tenacity
from google import genai
from google.genai import errors, types

# Add project root to the Python path
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# news_summarizer 는 import 시점에 config.json 의 GEMINI_API_KEY 로 Gemini 클라이언트를 만든다(파일이 없으면 import 실패).
if not os.path.exists(os.path.join(pjt_home_path, 'config.json')):
    pytest.skip("config.json 이 없어 news_summarizer 를 import 할 수 없습니다.", allow_module_level=True)

from src.services import news_summarizer

DUMMY_API_KEY = "dummy-api-key-for-unit-test-0123456789"

NEWS_ITEM = {"id": "news-1", "title": "삼성전자, 2나노 공정 양산 시작", "content": "삼성전자가 2나노 공정의 양산을 시작했다."}

OVERLOADED_MESSAGE = "The model is overloaded. Please try again later."


# --- 안전장치: 실제 API 호출 차단 ---

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
    httpx2 / httpx 의 실제 네트워크 전송도 막는다. summarize_news 는 모든 예외를 삼키므로,
    대역이 호출됐는지는 테스트가 끝난 뒤 따로 확인한다.
    """
    forbidden = _ForbiddenClient()
    monkeypatch.setattr(news_summarizer, "client", forbidden)

    def _no_network(self, request):
        raise RuntimeError(f"테스트에서 실제 네트워크 요청을 보냈습니다: {request.method} {request.url.host}")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", _no_network)
    try:
        import httpx
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    except ImportError:
        pass

    yield
    assert forbidden.models.calls == [], "대역 없이 news_summarizer.client 를 호출한 테스트가 있습니다."


# --- 클라이언트 대역 테스트: 요청 설정 확인 ---

def _ok_response(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        finish_reason=types.FinishReason.STOP)])


@pytest.fixture
def mock_models(mocker):
    """news_summarizer.client 를 MagicMock 으로 바꾼다."""
    return mocker.patch.object(news_summarizer, 'client').models


def test_summarize_news_request_arguments(mock_models):
    """모델명, 프롬프트(제목·내용·문장 수), 요청 설정을 넘기고, 응답의 앞뒤 공백을 지운 요약을 돌려준다."""
    mock_models.generate_content.return_value = _ok_response("\n  * 요약 1\n* 요약 2  \n")

    assert news_summarizer.summarize_news(NEWS_ITEM, num_sentences=5) == "* 요약 1\n* 요약 2"

    mock_models.generate_content.assert_called_once()
    kwargs = mock_models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert NEWS_ITEM["title"] in kwargs["contents"]
    assert NEWS_ITEM["content"] in kwargs["contents"]
    assert "5문장으로" in kwargs["contents"]
    assert kwargs["config"] is news_summarizer.GEMINI_REQUEST_CONFIG


def test_summarize_news_default_num_sentences_and_missing_fields(mock_models):
    """문장 수 기본값은 3, 제목·내용이 없으면 '제목 없음'·'내용 없음' 으로 채운다."""
    mock_models.generate_content.return_value = _ok_response("요약")

    assert news_summarizer.summarize_news({}) == "요약"
    contents = mock_models.generate_content.call_args.kwargs["contents"]
    assert "3문장으로" in contents
    assert "뉴스 제목: 제목 없음" in contents
    assert "뉴스 내용: 내용 없음" in contents


def test_request_config_values():
    """시도당 120초(120000ms), 503 만 최대 4회 시도(1초 시작, 1.3배, 최대 10초, jitter 1), 자동 이어받기 끔"""
    config = news_summarizer.GEMINI_REQUEST_CONFIG
    assert config.http_options.timeout == 120_000
    retry = config.http_options.retry_options
    assert retry.attempts == 4
    assert retry.initial_delay == 1.0
    assert retry.exp_base == 1.3
    assert retry.max_delay == 10.0
    assert retry.jitter == 1.0
    assert retry.http_status_codes == [503]
    assert config.automatic_continuation is False


@pytest.mark.parametrize("error, expected", [
    (errors.ServerError(503, {"error": {"code": 503, "message": OVERLOADED_MESSAGE, "status": "UNAVAILABLE"}}),
     f"요약 실패: 503 {OVERLOADED_MESSAGE}"),
    (errors.ClientError(429, {"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED",
                                        "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo",
                                                     "retryDelay": "28s"}]}}),
     "요약 실패: 429 quota"),
    (RuntimeError("boom"), "요약 실패: boom"),
    (httpx2.ReadTimeout("The read operation timed out"), "요약 실패: The read operation timed out"),
], ids=["503", "429", "other_exception", "read_timeout"])
def test_summarize_news_failure_text(mock_models, error, expected):
    """예외를 밖으로 내지 않고 '요약 실패: <설명>' 을 돌려준다. API 오류는 '<코드> <메시지>' 형태."""
    mock_models.generate_content.side_effect = error

    assert news_summarizer.summarize_news(NEWS_ITEM) == expected


@pytest.mark.parametrize("response", [
    types.GenerateContentResponse(
        prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY)),
    types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)]),
], ids=["prompt_blocked", "candidate_without_content"])
def test_summarize_news_response_without_text(mock_models, response):
    """응답에 텍스트가 없으면 '요약 실패: Gemini API 응답에 텍스트가 없습니다 (...)' 를 돌려준다."""
    mock_models.generate_content.return_value = response

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result.startswith("요약 실패: Gemini API 응답에 텍스트가 없습니다 (")
    assert "SAFETY" in result


# --- 실제 SDK 경로 테스트 (가짜 HTTP 전송 계층) ---
#
# 더미 API 키와 httpx2.MockTransport 를 쓰는 실제 genai.Client 로 SDK 코드(요청 설정 적용, 예외 변환,
# tenacity 재시도)를 그대로 거친다.
#   - 전송 계층 주입: HttpOptions(client_args={'transport': ...}) 는 그대로 httpx2.Client(**client_args) 에 전달된다
#     (google/genai/_api_client.py 의 BaseApiClient.__init__).
#   - SDK 의 재시도 대기(tenacity)는 tenacity.nap.sleep -> time.sleep 을 부르므로 tenacity.nap.time 을 대역으로 바꿔
#     실제로 기다리지 않고 대기 시간만 기록한다.

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


OVERLOADED_BODY = _error_body(503, "UNAVAILABLE", OVERLOADED_MESSAGE)
QUOTA_BODY = _error_body(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.", [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
     "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "28s"},
])


class FakeGeminiServer:
    """
    httpx2.MockTransport 의 핸들러. 받은 요청을 기록하고, 정해 둔 응답이나 예외를 차례로 돌려준다.
    목록을 다 쓰면 마지막 항목을 계속 돌려준다(예: '계속 503').
    """

    def __init__(self):
        self.items = []
        self.requests = []

    def respond(self, *items):
        """items: 예외 인스턴스(전송 계층에서 던짐) 또는 _http(...) 로 만든 응답 함수"""
        self.items = list(items)

    def handler(self, request):
        self.requests.append(request)
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, BaseException):
            raise item
        return item()


class FakeSleep:
    """tenacity.nap 이 쓰는 time 모듈 대역. SDK 재시도 대기를 기록만 한다."""

    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


@pytest.fixture
def sdk_news(mocker, monkeypatch):
    """news_summarizer.client 를 가짜 전송 계층을 쓰는 실제 genai.Client 로 바꾸고, SDK 재시도 대기를 기록만 하게 한다."""
    for name in ("GOOGLE_GENAI_HTTP_CLIENT", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_CLIENT_MODE",
                 "GOOGLE_GEMINI_BASE_URL", "GOOGLE_GENAI_API_VERSION"):
        monkeypatch.delenv(name, raising=False)

    sdk_sleep = FakeSleep()
    mocker.patch.object(tenacity.nap, 'time', sdk_sleep)
    server = FakeGeminiServer()
    sdk_client = genai.Client(
        api_key=DUMMY_API_KEY,
        vertexai=False,
        http_options=types.HttpOptions(client_args={"transport": httpx2.MockTransport(server.handler)}),
    )
    mocker.patch.object(news_summarizer, 'client', sdk_client)
    return server, sdk_sleep


def _assert_backoff(sleeps):
    """SDK 재시도 대기: n 번째 대기는 1.3**(n-1) 초 + 0~1초 무작위 (최대 10초)"""
    for n, seconds in enumerate(sleeps, start=1):
        base = 1.0 * 1.3 ** (n - 1)
        assert base <= seconds <= min(base + 1.0, 10.0)


def test_sdk_success_strips_and_sends_prompt(sdk_news):
    """정상 응답: 앞뒤 공백을 지운 요약을 돌려주고, 요청에 모델·프롬프트·시간 제한이 실린다."""
    server, sdk_sleep = sdk_news
    server.respond(_http(200, _ok_body("\n\n* 요약 1\n* 요약 2\n  ")))

    assert news_summarizer.summarize_news(NEWS_ITEM, num_sentences=4) == "* 요약 1\n* 요약 2"
    assert len(server.requests) == 1
    request = server.requests[0]
    assert request.url.path.endswith("/models/gemini-3.5-flash-lite:generateContent")
    prompt = json.loads(request.content)["contents"][0]["parts"][0]["text"]
    assert "4문장으로" in prompt
    assert NEWS_ITEM["title"] in prompt and NEWS_ITEM["content"] in prompt
    assert request.extensions["timeout"]["read"] == 120.0
    assert request.headers["x-server-timeout"] == "120"
    assert sdk_sleep.sleeps == []


def test_sdk_503_retried_up_to_four_requests(sdk_news):
    """503 이 계속되면 SDK 가 HTTP 요청 4회(시도마다 120초 제한) 후 '요약 실패: 503 <메시지>' 를 돌려준다."""
    server, sdk_sleep = sdk_news
    server.respond(_http(503, OVERLOADED_BODY))

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result == f"요약 실패: 503 {OVERLOADED_MESSAGE}"
    assert len(server.requests) == 4
    assert [request.extensions["timeout"]["read"] for request in server.requests] == [120.0] * 4
    assert [request.headers["x-server-timeout"] for request in server.requests] == ["120"] * 4
    assert len(sdk_sleep.sleeps) == 3
    _assert_backoff(sdk_sleep.sleeps)


def test_sdk_503_then_success(sdk_news):
    """503 두 번 뒤 성공하면 요약을 돌려준다."""
    server, sdk_sleep = sdk_news
    server.respond(_http(503, OVERLOADED_BODY), _http(503, OVERLOADED_BODY), _http(200, _ok_body(" 요약 ")))

    assert news_summarizer.summarize_news(NEWS_ITEM) == "요약"
    assert len(server.requests) == 3
    assert len(sdk_sleep.sleeps) == 2
    _assert_backoff(sdk_sleep.sleeps)


@pytest.mark.parametrize("code, body, expected_message", [
    (429, QUOTA_BODY, "You exceeded your current quota."),
    (400, _error_body(400, "INVALID_ARGUMENT", "Request contains an invalid argument."),
     "Request contains an invalid argument."),
    (500, _error_body(500, "INTERNAL", "An internal error has occurred."), "An internal error has occurred."),
    (504, _error_body(504, "DEADLINE_EXCEEDED", "Deadline expired before operation could complete."),
     "Deadline expired before operation could complete."),
], ids=["429", "400", "500", "504"])
def test_sdk_non_503_errors_not_retried(sdk_news, code, body, expected_message):
    """503 이 아닌 API 오류는 요청 1회 후 '요약 실패: <코드> <메시지>'. 응답 본문 dict 는 문구에 넣지 않는다."""
    server, sdk_sleep = sdk_news
    server.respond(_http(code, body))

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result == f"요약 실패: {code} {expected_message}"
    assert len(server.requests) == 1
    assert sdk_sleep.sleeps == []
    assert "{" not in result and "'error'" not in result and "details" not in result


@pytest.mark.parametrize("transport_error, expected_requests", [
    (httpx2.ReadTimeout("The read operation timed out"), 4),           # SDK 재시도 대상(TimeoutException)
    (httpx2.ConnectTimeout("timed out"), 4),                           # SDK 재시도 대상(TimeoutException)
    (httpx2.ConnectError("[Errno 111] Connection refused"), 4),        # SDK 재시도 대상(ConnectError)
    (httpx2.ReadError("[Errno 104] Connection reset by peer"), 1),     # SDK 재시도 대상 아님
    (httpx2.RemoteProtocolError("Server disconnected without sending a response."), 1),  # SDK 재시도 대상 아님
], ids=["read_timeout", "connect_timeout", "connect_error", "read_error", "remote_protocol_error"])
def test_sdk_transport_errors(sdk_news, transport_error, expected_requests):
    """
    retry_options 가 있으면 SDK 는 상태 코드와 상관없이 시간 초과·ConnectError 도 같은 횟수 안에서 재시도한다.
    그 밖의 전송 계층 오류는 재시도하지 않는다. 어느 쪽이든 예외 대신 '요약 실패: <예외 메시지>' 를 돌려준다.
    """
    server, sdk_sleep = sdk_news
    server.respond(transport_error)

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result == f"요약 실패: {transport_error}"
    assert len(server.requests) == expected_requests
    assert len(sdk_sleep.sleeps) == expected_requests - 1
    _assert_backoff(sdk_sleep.sleeps)


@pytest.mark.parametrize("body", [
    {"promptFeedback": {"blockReason": "SAFETY"}},
    {"candidates": [{"finishReason": "SAFETY"}]},
    {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "f", "args": {}}}]},
                     "finishReason": "STOP"}]},
], ids=["prompt_blocked", "candidate_without_content", "no_text_part"])
def test_sdk_response_without_text(sdk_news, body):
    """텍스트 없는 응답은 재시도 없이 '요약 실패: Gemini API 응답에 텍스트가 없습니다 (...)'."""
    server, sdk_sleep = sdk_news
    server.respond(_http(200, body))

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result.startswith("요약 실패: Gemini API 응답에 텍스트가 없습니다 (")
    assert len(server.requests) == 1
    assert sdk_sleep.sleeps == []


def test_sdk_does_not_follow_continuation(sdk_news):
    """응답이 CONTINUATION 으로 끝나도 후속 요청을 보내지 않는다(automatic_continuation=False)."""
    server, sdk_sleep = sdk_news
    server.respond(_http(200, _ok_body("앞부분", finish_reason="CONTINUATION", continuationToken="YWJj")),
                   _http(200, _ok_body("뒷부분")))

    assert news_summarizer.summarize_news(NEWS_ITEM) == "앞부분"
    assert len(server.requests) == 1


@pytest.mark.parametrize("failure", [
    _http(503, OVERLOADED_BODY),
    _http(400, _error_body(400, "INVALID_ARGUMENT", "API key not valid. Please pass a valid API key.")),
    httpx2.ConnectError("[Errno 111] Connection refused"),
], ids=["503", "400", "connect_error"])
def test_sdk_api_key_not_in_logs_or_result(sdk_news, caplog, failure):
    """오류 로그와 반환값에 API 키가 섞여 나오지 않는다."""
    server, sdk_sleep = sdk_news
    server.respond(failure)
    caplog.set_level("DEBUG")

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert server.requests[0].headers["x-goog-api-key"] == DUMMY_API_KEY
    assert result.startswith("요약 실패: ")
    assert DUMMY_API_KEY not in result
    assert caplog.records
    assert DUMMY_API_KEY not in caplog.text
    for record in caplog.records:
        assert DUMMY_API_KEY not in record.getMessage()
        assert DUMMY_API_KEY not in (record.exc_text or "")


def test_sdk_retries_do_not_really_sleep(sdk_news):
    """SDK 재시도 대기를 대역으로 바꿨으므로 503 4회도 실제로는 거의 시간이 걸리지 않는다."""
    server, sdk_sleep = sdk_news
    server.respond(_http(503, OVERLOADED_BODY))

    started = time.monotonic()
    news_summarizer.summarize_news(NEWS_ITEM)

    assert len(sdk_sleep.sleeps) == 3
    assert time.monotonic() - started < 1.0


# --- 실패 문구의 오류 설명(_describe_error): 본문 dict 제외, 상태 대체, 공백 정리, 길이 제한 ---

MAX_DESCRIPTION = 500  # news_summarizer.API_ERROR_DESCRIPTION_MAX_LENGTH 와 같아야 한다


def _bad_gateway_html():
    """프록시/로드밸런서가 돌려주는 수 KB 짜리 여러 줄 HTML 오류 페이지"""
    rows = "\n".join(f"      <li>upstream node {i:04d} did not respond in time</li>" for i in range(120))
    return ("<!DOCTYPE html>\n<html>\n  <head>\n    <title>502 Bad Gateway</title>\n  </head>\n  <body>\n"
            "    <h1>Bad Gateway</h1>\n    <ul>\n" + rows + "\n    </ul>\n  </body>\n</html>\n")


QUOTA_MULTILINE_MESSAGE = (
    "You exceeded your current quota, please check your plan and billing details. "
    "For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits.\n"
    "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, "
    "limit: 15, model: gemini-3.5-flash-lite\n"
    "* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, "
    "limit: 250000, model: gemini-3.5-flash-lite\n"
    "Please retry in 28.123456789s."
)


def test_description_max_length_constant():
    assert news_summarizer.API_ERROR_DESCRIPTION_MAX_LENGTH == MAX_DESCRIPTION


def test_sdk_empty_body_504_uses_status_without_dict(sdk_news):
    """본문 없는 504: SDK 는 message='' 와 status='Gateway Timeout' 을 만든다 -> 상태로 대체하고 dict 는 넣지 않는다."""
    server, sdk_sleep = sdk_news
    server.respond(lambda: httpx2.Response(504, text=""))

    assert news_summarizer.summarize_news(NEWS_ITEM) == "요약 실패: 504 Gateway Timeout"
    assert len(server.requests) == 1


def test_sdk_html_502_is_single_line_and_truncated(sdk_news):
    """수 KB 짜리 여러 줄 HTML 502: 한 줄로 합치고, 설명은 500자에서 잘라 '...' 를 붙인다."""
    html = _bad_gateway_html()
    collapsed = " ".join(html.split())
    assert len(html) > 4000 and "\n" in html
    assert not collapsed[MAX_DESCRIPTION - 1].isspace()  # 잘리는 자리가 공백이 아니어야 기대값이 명확하다
    server, sdk_sleep = sdk_news
    server.respond(lambda: httpx2.Response(502, text=html, headers={"content-type": "text/html"}))

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert "\n" not in result and "\r" not in result and "  " not in result
    assert result.startswith("요약 실패: 502 ")
    description = result[len("요약 실패: 502 "):]
    assert description == collapsed[:MAX_DESCRIPTION] + "..."
    assert len(description) == MAX_DESCRIPTION + 3
    assert "{" not in result
    assert len(server.requests) == 1


def test_sdk_multiline_quota_message_joined_without_truncation(sdk_news):
    """여러 줄로 된 400자 안팎의 429 메시지는 자르지 않고 한 줄로 합친다."""
    assert 350 <= len(QUOTA_MULTILINE_MESSAGE) <= MAX_DESCRIPTION and "\n" in QUOTA_MULTILINE_MESSAGE
    server, sdk_sleep = sdk_news
    server.respond(_http(429, {"error": {"code": 429, "message": QUOTA_MULTILINE_MESSAGE,
                                         "status": "RESOURCE_EXHAUSTED", "details": QUOTA_BODY["error"]["details"]}}))

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result == "요약 실패: 429 " + " ".join(QUOTA_MULTILINE_MESSAGE.split())
    assert "\n" not in result and not result.endswith("...")
    assert "Please retry in 28.123456789s." in result
    assert len(server.requests) == 1


@pytest.mark.parametrize("message, expected_description", [
    ("a" * MAX_DESCRIPTION, "a" * MAX_DESCRIPTION),                                  # 정확히 500자: 그대로
    ("a" * (MAX_DESCRIPTION + 1), "a" * MAX_DESCRIPTION + "..."),                    # 501자: 잘림
    ("a" * (MAX_DESCRIPTION - 1), "a" * (MAX_DESCRIPTION - 1)),                      # 499자: 그대로
    ("a " * 250 + "\n\n\t", ("a " * 250).strip()),                                   # 공백 정리 후 499자: 그대로
    (" \n ".join(["b" * 99] * 5) + "    \n", " ".join(["b" * 99] * 5)),             # 원문 512자, 정리 후 499자: 그대로
    ("  ".join(["c" * 100] * 5), " ".join(["c" * 100] * 5)[:MAX_DESCRIPTION] + "..."),  # 정리 후 504자: 잘림
], ids=["exactly_500", "501", "499", "spaces_collapsed_499", "long_raw_but_499_after_collapse", "504_after_collapse"])
def test_sdk_description_length_boundary(sdk_news, message, expected_description):
    """설명 길이는 공백을 정리한 뒤의 글자 수로 판단한다: 500자까지는 그대로, 넘으면 500자 + '...'."""
    server, sdk_sleep = sdk_news
    server.respond(_http(400, _error_body(400, "INVALID_ARGUMENT", message)))

    assert news_summarizer.summarize_news(NEWS_ITEM) == f"요약 실패: 400 {expected_description}"


@pytest.mark.parametrize("respond, expected", [
    (_http(503, {"error": {"code": 503, "status": "UNAVAILABLE"}}), "요약 실패: 503 UNAVAILABLE"),         # message 없음
    (_http(400, {"error": {"code": 400}}), "요약 실패: 400"),                                              # 둘 다 없음
    (_http(429, {"error": {"code": 429, "message": "  \n\t ", "status": "RESOURCE_EXHAUSTED"}}),
     "요약 실패: 429 RESOURCE_EXHAUSTED"),                                                                 # 공백뿐인 message
    (_http(400, {"error": {"code": 400, "message": "", "status": " \n "}}), "요약 실패: 400"),             # 둘 다 공백뿐
    (_http(500, {"error": {"code": 500, "message": None, "status": None}}), "요약 실패: 500"),            # 둘 다 null
    (_http(400, ["not", "an", "object"]), "요약 실패: 400"),                                               # 본문이 dict 아님
    (lambda: httpx2.Response(503, text=""), "요약 실패: 503 Service Unavailable"),                         # 본문 없는 503(재시도 후)
], ids=["status_only", "neither", "blank_message", "both_blank", "both_null", "non_object_body", "empty_body_503"])
def test_sdk_description_fallbacks(sdk_news, respond, expected):
    """message 가 비면 status, 둘 다 없으면 코드만 쓴다. 어느 경우에도 본문 dict 는 넣지 않는다."""
    server, sdk_sleep = sdk_news
    server.respond(respond)

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result == expected
    assert "{" not in result and "[" not in result


@pytest.mark.parametrize("error", [
    RuntimeError("첫 줄\n  둘째 줄\t\t탭"),
    ValueError("x" * 2000),
    httpx2.RemoteProtocolError("Server disconnected\nwithout sending a response."),
], ids=["multiline", "very_long", "transport_multiline"])
def test_non_api_error_description_is_str_unchanged(mock_models, error):
    """errors.APIError 가 아닌 예외는 공백 정리·자르기 없이 str(exc) 를 그대로 쓴다."""
    mock_models.generate_content.side_effect = error

    assert news_summarizer.summarize_news(NEWS_ITEM) == f"요약 실패: {error}"


def test_describe_error_directly():
    """_describe_error 단위 확인: APIError 는 '<코드> <설명>' 한 줄, 그 밖은 str(exc)"""
    describe = news_summarizer._describe_error
    assert describe(errors.ServerError(504, {"message": "", "status": "Gateway Timeout"})) == "504 Gateway Timeout"
    assert describe(errors.ClientError(400, {"error": {"code": 400, "message": "bad\n\nrequest"}})) == "400 bad request"
    assert describe(errors.ClientError(404, {})) == "404"
    assert describe(KeyError("k")) == str(KeyError("k"))


@pytest.mark.parametrize("respond", [
    lambda: httpx2.Response(504, text=""),
    lambda: httpx2.Response(502, text=_bad_gateway_html()),
    _http(429, {"error": {"code": 429, "message": QUOTA_MULTILINE_MESSAGE, "status": "RESOURCE_EXHAUSTED"}}),
    httpx2.RemoteProtocolError("Server disconnected\nwithout sending a response."),
], ids=["empty_504", "html_502", "multiline_429", "transport_error"])
def test_log_and_result_use_same_description(sdk_news, caplog, respond):
    """오류 로그와 반환 문구에 같은 설명이 들어간다."""
    server, sdk_sleep = sdk_news
    server.respond(respond)
    caplog.set_level("ERROR")

    result = news_summarizer.summarize_news(NEWS_ITEM)

    assert result.startswith("요약 실패: ")
    description = result[len("요약 실패: "):]
    logged = [record.getMessage() for record in caplog.records if record.name == news_summarizer.logger.name]
    assert logged == [f"뉴스 ID {NEWS_ITEM['id']} 요약 중 오류 발생: {description}"]
