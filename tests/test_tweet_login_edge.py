"""
쿠키 로그인 개선(tweet_scrapper_post)의 경계 조건·실패 경로 테스트.
가짜 쿠키 데이터만 사용하며 실제 브라우저·x.com·GCS 에 접근하지 않는다.
"""
import os
import json
import site
import logging
import pytest
import datetime as dt

from unittest.mock import MagicMock, patch
from selenium.common.exceptions import TimeoutException

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.abspath(os.path.join(src_path, os.pardir))
site.addsitedir(pjt_home_path)

from src.services import tweet_scrapper_post as tsp
from src.services.tweet_scrapper_post import TweetScraper

NOW = dt.datetime(2026, 11, 15, 0, 0, 0, tzinfo=dt.timezone.utc)
SECRET_AUTH = "SECRET-AUTH-VALUE-9f3a"
SECRET_CT0 = "SECRET-CT0-VALUE-77b1"


@pytest.fixture
def scraper():
    with patch('src.services.tweet_scrapper_post.ChromeDriverManager'), \
         patch('src.services.tweet_scrapper_post.webdriver.Chrome'):
        s = TweetScraper()
        s.driver = MagicMock()
        s.wait = MagicMock()
        s.actions = MagicMock()
        yield s


def cookies_with(auth_expiry, ct0_expiry=NOW.timestamp() + 30 * 86400, key="expirationDate"):
    return [{"name": "auth_token", "value": SECRET_AUTH, key: auth_expiry},
            {"name": "ct0", "value": SECRET_CT0, key: ct0_expiry}]


def by_level(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


# --- check_cookie_expiry 경계 ---

@pytest.mark.parametrize("delta_seconds, level, tag", [
    (-86400 * 3, logging.ERROR, "[쿠키 만료]"),
    (-1, logging.ERROR, "[쿠키 만료]"),
    (0, logging.ERROR, "[쿠키 만료]"),                       # 정확히 0일: 이미 만료로 본다
    (1, logging.WARNING, "[쿠키 만료 임박]"),
    (7 * 86400, logging.WARNING, "[쿠키 만료 임박]"),         # 정확히 7일: 임박
    (7 * 86400 + 1, logging.INFO, None),                    # 7일 초과 직후: 일반 INFO
])
@pytest.mark.parametrize("key", ["expirationDate", "expiry"])
def test_check_cookie_expiry_boundaries(scraper, caplog, delta_seconds, level, tag, key):
    caplog.set_level(logging.DEBUG)
    scraper.check_cookie_expiry(cookies_with(NOW.timestamp() + delta_seconds, key=key), now=NOW)

    auth_records = [r for r in caplog.records if "auth_token" in r.getMessage() and "쿠키" in r.getMessage()
                    and "만료" in r.getMessage()]
    assert len(auth_records) == 1
    assert auth_records[0].levelno == level
    if tag:
        assert tag in auth_records[0].getMessage()
    else:
        assert "[쿠키 만료" not in auth_records[0].getMessage()
    # 다른 쿠키(ct0, 30일 남음)는 영향을 받지 않는다.
    assert not any("ct0" in m for m in by_level(caplog, logging.ERROR) + by_level(caplog, logging.WARNING))


@pytest.mark.parametrize("raw, expected_level", [
    (str(int(NOW.timestamp()) + 3 * 86400), logging.WARNING),   # 숫자 문자열도 해석한다
    (str(int(NOW.timestamp()) - 3 * 86400), logging.ERROR),
    (-1, logging.ERROR),                                         # 음수: 1970년 -> 이미 만료
    (-1e12, None),                                               # 해석 범위를 벗어나도 예외가 나면 안 된다
])
def test_check_cookie_expiry_odd_numeric_values(scraper, caplog, raw, expected_level):
    scraper.check_cookie_expiry(cookies_with(raw), now=NOW)  # 예외가 올라오지 않아야 한다
    if expected_level is not None:
        recs = [r for r in caplog.records if "auth_token" in r.getMessage() and "만료" in r.getMessage()]
        assert recs and recs[0].levelno == expected_level


@pytest.mark.parametrize("raw", ["abc", "", [], {}, float("nan"), float("inf"), 1e30])
def test_check_cookie_expiry_unparsable_does_not_raise(scraper, caplog, raw):
    """만료 시각을 해석하지 못해도 예외 없이 지나가고, 다른 쿠키 점검은 계속된다."""
    caplog.set_level(logging.DEBUG)
    scraper.check_cookie_expiry(cookies_with(raw), now=NOW)

    assert any("ct0 쿠키 만료까지 30.0일" in m for m in by_level(caplog, logging.INFO))
    assert not any("[쿠키 만료]" in m for m in by_level(caplog, logging.ERROR))
    for r in caplog.records:
        assert SECRET_AUTH not in r.getMessage() and SECRET_CT0 not in r.getMessage()


def test_check_cookie_expiry_naive_now_is_not_required(scraper):
    """now 를 생략해도(현재 시각 사용) 동작한다."""
    scraper.check_cookie_expiry(cookies_with(dt.datetime.now(dt.timezone.utc).timestamp() + 86400))


# --- 쿠키 값이 로그에 섞이지 않는지 ---

def test_cookie_values_never_logged_in_any_path(scraper, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    cookie_file = tmp_path / "tweet_cookies.json"
    monkeypatch.setattr(tsp, "COOKIE_FILE", str(cookie_file))
    monkeypatch.setattr(tsp, "pjt_home_path", str(tmp_path))
    scraper.driver.page_source = "<html></html>"
    scraper.driver.current_url = "https://x.com/i/flow/login"

    def run(content, enforce, **patches):
        cookie_file.write_text(content, encoding="utf-8")
        monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", enforce)
        with patch('src.services.tweet_scrapper_post.time.sleep'):
            scraper.login_with_cookies()

    good = json.dumps(cookies_with(NOW.timestamp() - 86400))      # 만료된 쿠키
    for enforce in (False, True):
        scraper.wait.until.side_effect = TimeoutException()       # 로그인 확인 실패 경로 (실제 is_logged_in)
        run(good, enforce)
        scraper.wait.until.side_effect = None
        run(good, enforce)                                        # 로그인 화면으로 이동된 경로
        run(f'[{{"name": "auth_token", "value": "{SECRET_AUTH}"', enforce)   # 깨진 JSON (값이 파일에 있음)
        run(f'["{SECRET_AUTH}"]', enforce)                        # 형식 오류 (값이 파일에 있음)
        scraper.driver.add_cookie.side_effect = Exception("boom")
        run(good, enforce)                                        # 브라우저 오류 경로
        scraper.driver.add_cookie.side_effect = None

    assert caplog.records
    for r in caplog.records:
        text = r.getMessage() + (str(r.exc_text) if r.exc_text else "")
        assert SECRET_AUTH not in text and SECRET_CT0 not in text


# --- is_login_page_url 경계 ---

@pytest.mark.parametrize("url, expected", [
    (None, False),
    ("", False),
    ("https://x.com/login", True),
    ("https://x.com/login/", True),                       # 끝의 /
    ("https://x.com/login?lang=ko", True),                # 쿼리 문자열
    ("https://x.com/login#top", True),
    ("https://x.com/i/flow/login", True),
    ("https://x.com/i/flow/login/", True),
    ("https://x.com/i/flow/login?redirect_after_login=%2Fhome", True),
    ("https://x.com/loginfoo", False),                    # 비슷한 이름
    ("https://x.com/login_help", False),
    ("https://x.com/i/flow/loginx", False),
    ("https://x.com/i/flow/signup", False),
    ("https://x.com/home?next=/login", False),            # 쿼리에만 login
    ("https://x.com/home", False),
    ("https://x.com/user/login", False),
])
def test_is_login_page_url_edges(url, expected):
    assert tsp.is_login_page_url(url) is expected


# --- is_logged_in ---

def test_is_logged_in_navigates_before_waiting_and_uses_wait(scraper):
    order = []
    scraper.driver.get.side_effect = lambda url: order.append(("get", url))
    scraper.wait.until.side_effect = lambda cond: order.append(("wait", None)) or MagicMock()
    scraper.driver.current_url = "https://x.com/home"

    assert scraper.is_logged_in() is True
    assert order == [("get", "https://x.com/home"), ("wait", None)]


def test_is_logged_in_error_log_contains_url_and_guidance(scraper, caplog):
    scraper.wait.until.side_effect = TimeoutException()
    scraper.driver.current_url = "https://x.com/i/flow/login"

    assert scraper.is_logged_in() is False
    errs = by_level(caplog, logging.ERROR)
    assert len(errs) == 1
    assert "[쿠키 로그인 실패]" in errs[0] and "tweet_cookies.json" in errs[0] and "https://x.com/i/flow/login" in errs[0]


# --- scrape_target_users ---

def side_effect_from(results):
    def _f(user):
        r = results[user]
        if isinstance(r, Exception):
            raise r
        return r
    return _f


def test_consecutive_failures_reset_after_success_never_aborts(scraper, monkeypatch):
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)
    F = TimeoutException
    results = {"a": F(), "b": F(), "c": 1, "d": F(), "e": F(), "f": 0, "g": F(), "h": F()}
    with patch.object(scraper, "scrape_user_post", side_effect=side_effect_from(results)), \
         patch.object(scraper, "upload_posts_json_to_gcs") as up, \
         patch.object(scraper, "is_logged_in") as chk:
        total = scraper.scrape_target_users(list(results), "20261115", posts_json_upload=True)

    assert total == 1
    chk.assert_not_called()
    assert [c.args[0] for c in up.call_args_list] == ["c", "f"]      # 실패한 사용자는 업로드 안 함


def test_consecutive_failures_abort_ignores_enforce_flag_and_logs_summary(scraper, monkeypatch, caplog):
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", False)
    results = {"a": 2, "b": TimeoutException(), "c": TimeoutException(), "d": TimeoutException(), "e": 9}
    with patch.object(scraper, "scrape_user_post", side_effect=side_effect_from(results)), \
         patch.object(scraper, "upload_posts_json_to_gcs") as up, \
         patch.object(scraper, "is_logged_in", return_value=False) as chk:
        with pytest.raises(Exception, match=r"\[쿠키 로그인 실패\]"):
            scraper.scrape_target_users(list(results), "20261115", posts_json_upload=True)

    chk.assert_called_once()
    assert [c.args[0] for c in up.call_args_list] == ["a"]
    summaries = [m for m in by_level(caplog, logging.WARNING) + by_level(caplog, logging.INFO)
                 if m.startswith("수집 결과:")]
    assert len(summaries) == 1
    for token in ("성공 1명", "실패 3명", "b", "c", "d", "게시글 합계 2건"):
        assert token in summaries[0]


def test_three_targets_all_failing_reports_consecutive_failure_once(scraper, monkeypatch, caplog):
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)
    with patch.object(scraper, "scrape_user_post", side_effect=TimeoutException()), \
         patch.object(scraper, "is_logged_in", return_value=True) as chk:
        with pytest.raises(Exception, match=r"\[수집 연속 실패\]"):
            scraper.scrape_target_users(["a", "b", "c"], "20261115")
    chk.assert_called_once()
    assert sum("[수집 연속 실패]" in m for m in by_level(caplog, logging.ERROR)) == 1


def test_empty_target_list_is_not_failure(scraper):
    with patch.object(scraper, "is_logged_in") as chk:
        assert scraper.scrape_target_users([], "20261115") == 0
    chk.assert_not_called()


def test_zero_posts_with_two_targets_when_recheck_raises_still_aborts(scraper):
    with patch.object(scraper, "scrape_user_post", return_value=0), \
         patch.object(scraper, "is_logged_in", side_effect=RuntimeError("gone")):
        with pytest.raises(Exception, match=r"\[수집 게시글 0건\]"):
            scraper.scrape_target_users(["a", "b"], "20261115")


def test_one_user_with_posts_among_zero_post_users_is_ok(scraper):
    results = {"a": 0, "b": 0, "c": 1}
    with patch.object(scraper, "scrape_user_post", side_effect=side_effect_from(results)), \
         patch.object(scraper, "is_logged_in") as chk:
        assert scraper.scrape_target_users(list(results), "20261115") == 1
    chk.assert_not_called()


# --- login_error_page 업로드 / 저장 위치 ---

def test_upload_login_error_page_nonzero_return_only_warns(scraper, caplog):
    scraper.login_error_page_path = "/fake/data/login_error_page_000000.html"
    with patch('src.services.tweet_scrapper_post.gcs_upload_json.upload_local_file_to_gcs', return_value=2):
        scraper.upload_login_error_page_to_gcs("20261115")
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


def test_save_login_error_page_writes_under_data_dir(scraper, tmp_path, monkeypatch):
    monkeypatch.setattr(tsp, "pjt_home_path", str(tmp_path))
    scraper.driver.page_source = "<html>x</html>"
    path = scraper.save_login_error_page()
    assert os.path.dirname(path) == str(tmp_path / "data")
    assert os.path.basename(path).startswith("login_error_page_") and path.endswith(".html")
    assert not (tmp_path / "login_error_page.html").exists()


def test_no_mail_or_notification_in_scraper_module():
    src = open(tsp.__file__, encoding="utf-8").read()
    assert "send_mail" not in src and "smtplib" not in src
