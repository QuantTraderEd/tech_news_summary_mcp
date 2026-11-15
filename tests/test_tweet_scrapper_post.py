import os
import re
import site
import json
import logging
import pytest
import datetime as dt

from unittest.mock import MagicMock, patch, mock_open
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# Add project root to the Python path to allow importing from 'app'
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services import tweet_scrapper_post as tsp
from src.services.tweet_scrapper_post import TweetScraper


@pytest.fixture
def scraper():
    """Pytest fixture to create a TweetScraper instance with mocked dependencies."""
    with patch('src.services.tweet_scrapper_post.ChromeDriverManager'), \
         patch('src.services.tweet_scrapper_post.webdriver.Chrome'):
        
        scraper_instance = TweetScraper()
        # Assign mock driver and wait objects directly to the instance
        scraper_instance.driver = MagicMock()
        scraper_instance.wait = MagicMock()
        scraper_instance.actions = MagicMock()
        yield scraper_instance

def test_set_target_date_range(scraper):
    """Test if the date range is set correctly."""
    start_date = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc)
    end_date = dt.datetime(2023, 1, 31, tzinfo=dt.timezone.utc)
    scraper.set_target_date_range(start_date, end_date)
    assert scraper.start_date == start_date
    assert scraper.end_date == end_date

def test_parse_tweet_datetime():
    """Test parsing of tweet datetime strings."""
    # Test valid datetime string
    valid_str = "2025-06-03T22:14:35.000Z"
    expected_dt = dt.datetime(2025, 6, 3, 22, 14, 35, tzinfo=dt.timezone.utc)
    assert TweetScraper().parse_tweet_datetime(valid_str) == expected_dt

    # Test invalid datetime string
    invalid_str = "not-a-valid-date"
    # The function is designed to return an (aware) dt.datetime.min on failure
    failed_dt = TweetScraper().parse_tweet_datetime(invalid_str)
    assert failed_dt == dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    # aware datetime 인 start_date 와 예외 없이 비교 가능해야 한다.
    assert failed_dt < dt.datetime.now(dt.timezone.utc)

@patch('src.services.tweet_scrapper_post.time.sleep')
def test_login_to_twitter_success(mock_sleep, scraper):
    """Test successful login scenario."""
    mock_user_input = MagicMock()
    mock_next_button = MagicMock()
    mock_password_input = MagicMock()
    mock_password_input.get_attribute.return_value = 'password'
    mock_login_button = MagicMock()
    
    # Configure the wait.until to return the mock elements in sequence
    scraper.wait.until.side_effect = [
        mock_user_input,
        mock_next_button,
        mock_password_input,  # For the dynamic step check
        mock_login_button,    # For the login button
        True                  # For the final login confirmation
    ]

    result = scraper.login_to_twitter("testuser", "testpass", "verification_info")
    
    assert result is True
    scraper.driver.get.assert_called_once_with("https://x.com/login")
    # 사용자 이름은 human_like_typing 으로 한 글자씩 입력된다.
    typed_username = "".join(call.args[0] for call in mock_user_input.send_keys.call_args_list)
    assert typed_username == "testuser"
    # 입력창/Next 버튼 클릭은 ActionChains 를 통해 수행된다.
    scraper.actions.move_to_element.assert_any_call(mock_user_input)
    scraper.actions.move_to_element.assert_any_call(mock_next_button)
    mock_password_input.send_keys.assert_called_once_with("testpass")
    mock_login_button.click.assert_called_once()

@patch('src.services.tweet_scrapper_post.time.sleep')
@patch('src.services.tweet_scrapper_post.open', new_callable=mock_open)
def test_login_to_twitter_failure_on_timeout(mock_file_open, mock_sleep, scraper):
    """Test failed login due to a TimeoutException."""
    scraper.wait.until.side_effect = TimeoutException("Element not found.")
    scraper.driver.page_source = "<html>error page</html>"

    result = scraper.login_to_twitter("testuser", "testpass", "info")
    assert result is False

    # 오류 페이지 덤프는 목 처리된 open 으로만 기록되어야 한다 (실제 파일을 건드리지 않음).
    expected_filepath = f"{pjt_home_path}/login_error_page.html"
    mock_file_open.assert_called_once_with(expected_filepath, "w", encoding="utf-8")
    mock_file_open().write.assert_called_once_with("<html>error page</html>")

@patch('src.services.tweet_scrapper_post.time.sleep')
@patch('src.services.tweet_scrapper_post.open', new_callable=mock_open)
@patch('src.services.tweet_scrapper_post.json.dump')
def test_scrape_user_post_with_date_filtering(mock_json_dump, mock_file_open, mock_sleep, scraper):
    """Test successful scraping and that posts are filtered by date."""
    target_username = "testuser"
    
    # --- Mocking Selenium WebElements ---
    # Mock post 1 (in date range)
    mock_time1 = MagicMock()
    mock_time1.get_attribute.return_value = "2025-06-28T12:00:00.000Z"
    mock_link1 = MagicMock()
    mock_link1.get_attribute.return_value = f"https://x.com/{target_username}/status/1"
    mock_link1.find_element.return_value = mock_time1
    mock_text1 = MagicMock()
    mock_text1.text = "This is a recent tweet."
    
    mock_article1 = MagicMock()
    def find_element_side_effect_1(*args, **kwargs):
        if ".//a[time]" in args[1]: return mock_link1
        if ".//div[@data-testid='tweetText']" in args[1]: return mock_text1
        raise NoSuchElementException
    mock_article1.find_element.side_effect = find_element_side_effect_1

    # Mock post 2 (out of date range)
    mock_time2 = MagicMock()
    mock_time2.get_attribute.return_value = "2024-01-01T12:00:00.000Z"
    mock_link2 = MagicMock()
    mock_link2.get_attribute.return_value = f"https://x.com/{target_username}/status/2"
    mock_link2.find_element.return_value = mock_time2
    mock_text2 = MagicMock()
    mock_text2.text = "This is an old tweet."
    
    mock_article2 = MagicMock()
    def find_element_side_effect_2(*args, **kwargs):
        if ".//a[time]" in args[1]: return mock_link2
        if ".//div[@data-testid='tweetText']" in args[1]: return mock_text2
        raise NoSuchElementException
    mock_article2.find_element.side_effect = find_element_side_effect_2

    scraper.driver.find_elements.return_value = [mock_article1, mock_article2]
    
    # Set a date range that includes post 1 but not post 2
    start_date = dt.datetime(2025, 6, 27, tzinfo=dt.timezone.utc)
    end_date = dt.datetime(2025, 6, 29, tzinfo=dt.timezone.utc)
    scraper.set_target_date_range(start_date, end_date)
    
    post_count = scraper.scrape_user_post(target_username)

    # 기간 안의 게시글 1건만 수집되고, 그 수를 반환한다.
    assert post_count == 1

    # Assert that the profile page was loaded
    scraper.driver.get.assert_called_with(f"https://x.com/{target_username}")
    
    # Assert file was opened for writing
    expected_filepath = f"{pjt_home_path}/data/{target_username}_posts.json"
    mock_file_open.assert_called_once_with(expected_filepath, 'w', encoding='utf-8')

    # Assert json.dump was called with only the correctly filtered post data
    expected_post_data = {
        'data': [{
            'url': f"https://x.com/{target_username}/status/1",
            'id': '1',
            'created_at': "2025-06-28T12:00:00.000Z",
            'text': "This is a recent tweet."
        }]
    }
    mock_json_dump.assert_called_once_with(expected_post_data, mock_file_open(), ensure_ascii=False, indent=4)

@patch('src.services.tweet_scrapper_post.time.sleep')
@patch('src.services.tweet_scrapper_post.open', new_callable=mock_open)
@patch('src.services.tweet_scrapper_post.json.dump')
def test_scrape_user_post_skips_unparsable_timestamp(mock_json_dump, mock_file_open, mock_sleep, scraper):
    """작성 시간을 파싱할 수 없는 게시글은 예외 없이 건너뛰어야 한다."""
    target_username = "testuser"

    mock_time = MagicMock()
    mock_time.get_attribute.return_value = "not-a-valid-date"
    mock_link = MagicMock()
    mock_link.get_attribute.return_value = f"https://x.com/{target_username}/status/1"
    mock_link.find_element.return_value = mock_time
    mock_text = MagicMock()
    mock_text.text = "Tweet with broken timestamp."

    mock_article = MagicMock()
    def find_element_side_effect(*args, **kwargs):
        if ".//a[time]" in args[1]: return mock_link
        if ".//div[@data-testid='tweetText']" in args[1]: return mock_text
        raise NoSuchElementException
    mock_article.find_element.side_effect = find_element_side_effect

    scraper.driver.find_elements.return_value = [mock_article]
    scraper.set_target_date_range(dt.datetime(2025, 6, 27, tzinfo=dt.timezone.utc),
                                  dt.datetime(2025, 6, 29, tzinfo=dt.timezone.utc))

    scraper.scrape_user_post(target_username)

    mock_file_open.assert_not_called()
    mock_json_dump.assert_not_called()

@patch('src.services.tweet_scrapper_post.time.sleep')
@patch('src.services.tweet_scrapper_post.open', new_callable=mock_open)
@patch('src.services.tweet_scrapper_post.json.dump')
def test_scrape_user_post_no_posts_found(mock_json_dump, mock_file_open, mock_sleep, scraper):
    """Test scraping when no posts are found on the page."""
    target_username = "nopostuser"
    
    # Simulate find_elements returning an empty list
    scraper.driver.find_elements.return_value = []

    post_count = scraper.scrape_user_post(target_username)

    # 게시글이 없으면 예외 없이 0 을 반환한다.
    assert post_count == 0

    # Assert that no file was opened and no data was dumped because no posts were collected
    mock_file_open.assert_not_called()
    mock_json_dump.assert_not_called()


# ---------------------------------------------------------------------------
# 쿠키 로그인 / 로그인 확인 / 쿠키 만료 경고 / 사용자 목록 수집 방어
# (가짜 쿠키 데이터만 사용하며, 실제 브라우저·x.com·GCS 에 접근하지 않는다)
# ---------------------------------------------------------------------------

FAKE_AUTH_TOKEN_VALUE = "fake-auth-token-value-for-test"
FAKE_CT0_VALUE = "fake-ct0-value-for-test"
FAKE_COOKIE_VALUES = (FAKE_AUTH_TOKEN_VALUE, FAKE_CT0_VALUE)
FIXED_NOW = dt.datetime(2026, 11, 15, 0, 0, 0, tzinfo=dt.timezone.utc)


def make_fake_cookies(now: dt.datetime, auth_token_days: float = 30, ct0_days: float = 30) -> list:
    """브라우저 확장으로 내보낸 형식(expirationDate: epoch 초)의 가짜 쿠키 목록"""
    return [
        {"name": "auth_token", "value": FAKE_AUTH_TOKEN_VALUE, "domain": ".x.com", "path": "/",
         "sameSite": "no_restriction", "expirationDate": (now + dt.timedelta(days=auth_token_days)).timestamp()},
        {"name": "ct0", "value": FAKE_CT0_VALUE, "domain": ".x.com", "path": "/",
         "sameSite": "Lax", "expirationDate": (now + dt.timedelta(days=ct0_days)).timestamp()},
        {"name": "guest_id", "value": "fake-guest-id", "domain": ".x.com", "path": "/"},
    ]


def messages(caplog, level: int) -> list:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


CRITICAL_PREFIX = "[쿠키 로그인 실패] 쿠키 로그인에 실패해 수집을 진행할 수 없습니다."


def assert_no_cookie_value_logged(caplog):
    """쿠키 값은 어떤 로그에도 남지 않아야 한다."""
    for record in caplog.records:
        text = record.getMessage()
        for value in FAKE_COOKIE_VALUES:
            assert value not in text


@pytest.fixture
def login_env(scraper, tmp_path, monkeypatch):
    """login_with_cookies 테스트용: 가짜 쿠키 파일과 임시 프로젝트 경로(data/ 저장 위치)를 준비한다."""
    cookie_path = tmp_path / "tweet_cookies.json"
    cookie_path.write_text(json.dumps(make_fake_cookies(dt.datetime.now(dt.timezone.utc))), encoding="utf-8")
    monkeypatch.setattr(tsp, "COOKIE_FILE", str(cookie_path))
    monkeypatch.setattr(tsp, "pjt_home_path", str(tmp_path))
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", False)
    scraper.driver.page_source = "<html>logged out page</html>"
    with patch('src.services.tweet_scrapper_post.time.sleep'):
        yield tmp_path


# --- is_login_page_url ---

@pytest.mark.parametrize("url, expected", [
    ("https://x.com/login", True),
    ("https://x.com/i/flow/login", True),
    ("https://x.com/i/flow/login?redirect_after_login=%2Fhome", True),
    ("https://x.com/home", False),
    ("https://x.com/", False),
    ("https://x.com/someone_login", False),
    ("", False),
])
def test_is_login_page_url(url, expected):
    assert tsp.is_login_page_url(url) is expected


# --- is_logged_in ---

def test_is_logged_in_success(scraper, caplog):
    """로그인 상태 요소가 나타나고 로그인 화면이 아니면 로그인된 것으로 판정한다."""
    scraper.wait.until.return_value = MagicMock()
    scraper.driver.current_url = "https://x.com/home"

    with patch('src.services.tweet_scrapper_post.EC.presence_of_element_located') as mock_presence:
        assert scraper.is_logged_in() is True

    scraper.driver.get.assert_called_once_with("https://x.com/home")
    # 세 요소 중 하나라도 나타나기를 기다린다.
    mock_presence.assert_called_once_with((By.XPATH, tsp.LOGGED_IN_ELEMENT_XPATH))
    for testid in ("AppTabBar_Home_Link", "SideNav_AccountSwitcher_Button", "primaryColumn"):
        assert testid in tsp.LOGGED_IN_ELEMENT_XPATH
    assert any("로그인 상태 확인 성공" in m and "https://x.com/home" in m for m in messages(caplog, logging.INFO))
    assert not messages(caplog, logging.ERROR)


def test_is_logged_in_fails_when_element_not_found(scraper, caplog):
    """로그인 상태 요소를 찾지 못하면(TimeoutException) 로그인되지 않은 것으로 판정한다."""
    scraper.wait.until.side_effect = TimeoutException("not found")
    scraper.driver.current_url = "https://x.com/"

    assert scraper.is_logged_in() is False

    errors = messages(caplog, logging.ERROR)
    assert any("[쿠키 로그인 실패]" in m and "tweet_cookies.json" in m and "https://x.com/" in m for m in errors)


@pytest.mark.parametrize("login_url", [
    "https://x.com/login",
    "https://x.com/i/flow/login?redirect_after_login=%2Fhome",
])
def test_is_logged_in_fails_on_login_page(scraper, caplog, login_url):
    """요소를 찾았더라도 로그인 화면으로 이동했으면 로그인되지 않은 것으로 판정한다."""
    scraper.wait.until.return_value = MagicMock()
    scraper.driver.current_url = login_url

    assert scraper.is_logged_in() is False
    assert any("[쿠키 로그인 실패]" in m and login_url in m for m in messages(caplog, logging.ERROR))


# --- login_with_cookies ---

def test_login_with_cookies_success(scraper, login_env, caplog):
    with patch.object(scraper, "is_logged_in", return_value=True) as mock_check:
        assert scraper.login_with_cookies() is True

    mock_check.assert_called_once()
    scraper.driver.get.assert_called_once_with("https://x.com")
    scraper.driver.refresh.assert_called_once()

    # 쿠키는 expirationDate -> expiry(int) 로 바꿔 적용하고, 허용되지 않는 sameSite 값은 제거한다.
    added = [c.args[0] for c in scraper.driver.add_cookie.call_args_list]
    assert [c["name"] for c in added] == ["auth_token", "ct0", "guest_id"]
    assert all("expirationDate" not in c for c in added)
    assert isinstance(added[0]["expiry"], int)
    assert "sameSite" not in added[0]
    assert added[1]["sameSite"] == "Lax"

    # 성공 시에는 화면 소스를 저장하지 않는다.
    assert scraper.login_error_page_path is None
    assert not (login_env / "data").exists()
    assert not messages(caplog, logging.ERROR)
    assert not messages(caplog, logging.CRITICAL)
    assert_no_cookie_value_logged(caplog)


def test_login_with_cookies_observe_mode_continues_on_check_failure(scraper, login_env, caplog):
    """관찰 모드: 로그인 확인이 실패해도 화면 소스만 저장하고 True 를 반환한다."""
    assert tsp.ENFORCE_LOGIN_CHECK is False  # login_env 가 모드를 명시적으로 patch 한다.

    with patch.object(scraper, "is_logged_in", return_value=False):
        assert scraper.login_with_cookies() is True

    saved = list((login_env / "data").glob("login_error_page_*.html"))
    assert len(saved) == 1
    assert re.fullmatch(r"login_error_page_\d{6}\.html", saved[0].name)
    assert saved[0].read_text(encoding="utf-8") == "<html>logged out page</html>"
    assert scraper.login_error_page_path == str(saved[0])
    assert any("관찰 모드" in m for m in messages(caplog, logging.WARNING))
    assert not messages(caplog, logging.CRITICAL)
    assert_no_cookie_value_logged(caplog)


def test_login_with_cookies_enforce_mode_fails_on_check_failure(scraper, login_env, monkeypatch, caplog):
    """강제 모드: 로그인 확인이 실패하면 화면 소스를 저장하고 False 를 반환한다."""
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", True)

    with patch.object(scraper, "is_logged_in", return_value=False):
        assert scraper.login_with_cookies() is False

    assert len(list((login_env / "data").glob("login_error_page_*.html"))) == 1
    assert scraper.login_error_page_path is not None
    crit = messages(caplog, logging.CRITICAL)
    assert len(crit) == 1
    assert crit[0].startswith(CRITICAL_PREFIX) and "강제 모드" in crit[0] and "로그인 상태 확인 실패" in crit[0]
    assert_no_cookie_value_logged(caplog)


def test_login_with_cookies_enforce_mode_succeeds_when_logged_in(scraper, login_env, monkeypatch):
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", True)

    with patch.object(scraper, "is_logged_in", return_value=True):
        assert scraper.login_with_cookies() is True


@pytest.mark.parametrize("enforce", [False, True])
def test_login_with_cookies_page_save_failure_does_not_mask_result(scraper, login_env, monkeypatch, caplog, enforce):
    """화면 소스 저장이 실패해도 경고만 남기고, 원래의 로그인 실패 처리(모드별 반환값)는 그대로다."""
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", enforce)
    # 프로젝트 경로를 일반 파일로 지정해 data/ 디렉토리를 만들 수 없게 한다.
    not_a_dir = login_env / "not_a_dir"
    not_a_dir.write_text("x")
    monkeypatch.setattr(tsp, "pjt_home_path", str(not_a_dir))

    with patch.object(scraper, "is_logged_in", return_value=False):
        assert scraper.login_with_cookies() is (not enforce)

    assert scraper.login_error_page_path is None
    assert any("화면 소스 저장 중 오류" in m for m in messages(caplog, logging.WARNING))


@pytest.mark.parametrize("enforce", [False, True])
def test_login_with_cookies_fails_when_cookie_file_missing(scraper, login_env, monkeypatch, caplog, enforce):
    """쿠키 파일이 없으면 모드와 무관하게 False 를 반환하고 브라우저 작업을 하지 않는다."""
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", enforce)
    monkeypatch.setattr(tsp, "COOKIE_FILE", str(login_env / "no_such_cookies.json"))

    with patch.object(scraper, "is_logged_in") as mock_check:
        assert scraper.login_with_cookies() is False

    mock_check.assert_not_called()
    scraper.driver.get.assert_not_called()
    assert any("[쿠키 파일 오류]" in m for m in messages(caplog, logging.ERROR))
    crit = messages(caplog, logging.CRITICAL)
    assert len(crit) == 1 and crit[0].startswith(CRITICAL_PREFIX) and "쿠키 파일 오류" in crit[0]


@pytest.mark.parametrize("enforce", [False, True])
@pytest.mark.parametrize("content", [
    '[{"name": "auth_token", "value": ',  # JSON 이 깨짐
    '{"name": "auth_token"}',             # 리스트가 아님
    '[]',                                 # 쿠키가 없음
    '["auth_token"]',                     # 쿠키 dict 가 아님
])
def test_login_with_cookies_fails_when_cookie_file_invalid(scraper, login_env, monkeypatch, caplog, enforce, content):
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", enforce)
    (login_env / "tweet_cookies.json").write_text(content, encoding="utf-8")

    with patch.object(scraper, "is_logged_in") as mock_check:
        assert scraper.login_with_cookies() is False

    mock_check.assert_not_called()
    scraper.driver.add_cookie.assert_not_called()
    assert any("[쿠키 파일 오류]" in m for m in messages(caplog, logging.ERROR))
    crit = messages(caplog, logging.CRITICAL)
    assert len(crit) == 1 and crit[0].startswith(CRITICAL_PREFIX) and "쿠키 파일 오류" in crit[0]


def test_login_with_cookies_fails_on_unexpected_browser_error(scraper, login_env, caplog):
    """쿠키 적용 중 예외가 나면 관찰 모드여도 False 를 반환한다(기존 동작 유지)."""
    scraper.driver.add_cookie.side_effect = Exception("invalid cookie domain")

    assert scraper.login_with_cookies() is False
    assert any("[쿠키 로그인 실패]" in m for m in messages(caplog, logging.ERROR))
    crit = messages(caplog, logging.CRITICAL)
    assert len(crit) == 1 and crit[0].startswith(CRITICAL_PREFIX) and "쿠키 적용 중 오류" in crit[0]
    assert_no_cookie_value_logged(caplog)


def test_login_with_cookies_expired_cookie_only_warns(scraper, login_env, caplog):
    """쿠키 만료 점검은 경고만 하고 로그인 진행 여부에는 영향을 주지 않는다."""
    expired = make_fake_cookies(dt.datetime.now(dt.timezone.utc), auth_token_days=-1, ct0_days=2)
    (login_env / "tweet_cookies.json").write_text(json.dumps(expired), encoding="utf-8")

    with patch.object(scraper, "is_logged_in", return_value=True) as mock_check:
        assert scraper.login_with_cookies() is True

    mock_check.assert_called_once()
    assert any("[쿠키 만료] auth_token" in m for m in messages(caplog, logging.ERROR))
    assert any("[쿠키 만료 임박] ct0" in m for m in messages(caplog, logging.WARNING))
    assert_no_cookie_value_logged(caplog)


# --- check_cookie_expiry ---

def test_check_cookie_expiry_expired_and_expiring_soon(scraper, caplog):
    cookies = make_fake_cookies(FIXED_NOW, auth_token_days=-2, ct0_days=3)

    scraper.check_cookie_expiry(cookies, now=FIXED_NOW)

    assert any("[쿠키 만료] auth_token 쿠키가 2.0일 전에 만료됐습니다" in m for m in messages(caplog, logging.ERROR))
    assert any("[쿠키 만료 임박] ct0 쿠키 만료까지 3.0일 남았습니다" in m for m in messages(caplog, logging.WARNING))
    assert_no_cookie_value_logged(caplog)


def test_check_cookie_expiry_far_future_is_info(scraper, caplog):
    cookies = make_fake_cookies(FIXED_NOW, auth_token_days=30, ct0_days=30)

    scraper.check_cookie_expiry(cookies, now=FIXED_NOW)

    infos = messages(caplog, logging.INFO)
    assert any("auth_token 쿠키 만료까지 30.0일 남았습니다" in m for m in infos)
    assert any("ct0 쿠키 만료까지 30.0일 남았습니다" in m for m in infos)
    assert not messages(caplog, logging.WARNING)
    assert not messages(caplog, logging.ERROR)
    assert_no_cookie_value_logged(caplog)


def test_check_cookie_expiry_warning_boundary(scraper, monkeypatch, caplog):
    """COOKIE_EXPIRY_WARNING_DAYS 이내(같은 값 포함)이면 WARNING, 넘으면 INFO 로 남긴다."""
    monkeypatch.setattr(tsp, "COOKIE_EXPIRY_WARNING_DAYS", 7)
    cookies = make_fake_cookies(FIXED_NOW, auth_token_days=7, ct0_days=7.5)

    scraper.check_cookie_expiry(cookies, now=FIXED_NOW)

    assert any("[쿠키 만료 임박] auth_token" in m for m in messages(caplog, logging.WARNING))
    assert not any("ct0" in m for m in messages(caplog, logging.WARNING))
    assert any("ct0 쿠키 만료까지 7.5일 남았습니다" in m for m in messages(caplog, logging.INFO))


def test_check_cookie_expiry_uses_expiry_key(scraper, caplog):
    """expirationDate 대신 expiry(int) 키만 있어도 만료 시각을 계산한다."""
    cookies = [
        {"name": "auth_token", "value": FAKE_AUTH_TOKEN_VALUE,
         "expiry": int((FIXED_NOW - dt.timedelta(days=1)).timestamp())},
        {"name": "ct0", "value": FAKE_CT0_VALUE,
         "expiry": int((FIXED_NOW + dt.timedelta(days=5)).timestamp())},
    ]

    scraper.check_cookie_expiry(cookies, now=FIXED_NOW)

    assert any("[쿠키 만료] auth_token 쿠키가 1.0일 전에 만료됐습니다" in m for m in messages(caplog, logging.ERROR))
    assert any("[쿠키 만료 임박] ct0 쿠키 만료까지 5.0일 남았습니다" in m for m in messages(caplog, logging.WARNING))


def test_check_cookie_expiry_missing_and_session_cookie(scraper, caplog):
    """쿠키가 없으면 ERROR, 만료 정보가 없는 세션 쿠키면 INFO 로 남긴다."""
    cookies = [{"name": "auth_token", "value": FAKE_AUTH_TOKEN_VALUE, "domain": ".x.com"}]

    scraper.check_cookie_expiry(cookies, now=FIXED_NOW)

    assert any("auth_token" in m and "세션 쿠키" in m for m in messages(caplog, logging.INFO))
    errors = messages(caplog, logging.ERROR)
    assert len(errors) == 1
    assert "[쿠키 누락]" in errors[0] and "ct0" in errors[0]
    assert_no_cookie_value_logged(caplog)


# --- upload_login_error_page_to_gcs ---

def test_upload_login_error_page_to_gcs(scraper):
    scraper.login_error_page_path = "/fake/data/login_error_page_010203.html"

    with patch('src.services.tweet_scrapper_post.gcs_upload_json.upload_local_file_to_gcs', return_value=0) as mock_upload:
        scraper.upload_login_error_page_to_gcs("20261115")

    mock_upload.assert_called_once_with("/fake/data/login_error_page_010203.html", date_str="20261115")


def test_upload_login_error_page_to_gcs_skips_without_saved_page(scraper):
    scraper.login_error_page_path = None

    with patch('src.services.tweet_scrapper_post.gcs_upload_json.upload_local_file_to_gcs') as mock_upload:
        scraper.upload_login_error_page_to_gcs("20261115")

    mock_upload.assert_not_called()


def test_upload_login_error_page_to_gcs_only_warns_on_error(scraper, caplog):
    scraper.login_error_page_path = "/fake/data/login_error_page_010203.html"

    with patch('src.services.tweet_scrapper_post.gcs_upload_json.upload_local_file_to_gcs',
               side_effect=Exception("gcs down")):
        scraper.upload_login_error_page_to_gcs("20261115")  # 예외가 올라오지 않아야 한다

    assert any("업로드 중 오류" in m for m in messages(caplog, logging.WARNING))


# --- scrape_target_users (사용자 목록 모드 방어) ---

def make_scrape_side_effect(results: dict):
    """사용자별 결과(게시글 수 또는 예외)를 돌려주는 scrape_user_post 대역"""
    def _scrape(user):
        result = results[user]
        if isinstance(result, Exception):
            raise result
        return result
    return _scrape


def test_scrape_target_users_skips_failed_user_and_continues(scraper, caplog):
    results = {"u1": 2, "u2": TimeoutException("profile timeout"), "u3": 0, "u4": 1}

    with patch.object(scraper, "scrape_user_post", side_effect=make_scrape_side_effect(results)) as mock_scrape, \
         patch.object(scraper, "upload_posts_json_to_gcs") as mock_upload, \
         patch.object(scraper, "is_logged_in") as mock_check:
        total = scraper.scrape_target_users(list(results), "20261115", posts_json_upload=True)

    assert total == 3
    assert mock_scrape.call_count == 4
    # 실패한 사용자는 GCS 업로드도 하지 않는다.
    assert [c.args[0] for c in mock_upload.call_args_list] == ["u1", "u3", "u4"]
    mock_check.assert_not_called()
    assert any("[u2]" in m and "건너뜁니다" in m for m in messages(caplog, logging.ERROR))
    # 루프 종료 후 결과 요약 한 줄
    summary = [r.getMessage() for r in caplog.records if r.getMessage().startswith("수집 결과:")]
    assert len(summary) == 1
    assert "대상 4명" in summary[0] and "성공 3명" in summary[0] and "실패 1명" in summary[0]
    assert "u2" in summary[0] and "게시글 합계 3건" in summary[0]


def test_scrape_target_users_no_upload_when_disabled(scraper):
    with patch.object(scraper, "scrape_user_post", return_value=1), \
         patch.object(scraper, "upload_posts_json_to_gcs") as mock_upload:
        assert scraper.scrape_target_users(["u1", "u2"], "20261115", posts_json_upload=False) == 2

    mock_upload.assert_not_called()


@pytest.mark.parametrize("login_state, expected_tag", [
    (False, "[쿠키 로그인 실패]"),
    (True, "[수집 연속 실패]"),
    (RuntimeError("browser is gone"), "[수집 연속 실패]"),
])
def test_scrape_target_users_aborts_after_consecutive_failures(scraper, monkeypatch, caplog, login_state, expected_tag):
    """연속 MAX_CONSECUTIVE_SCRAPE_FAILURES 명이 실패하면 로그인 상태를 재확인한 뒤 예외로 중단한다."""
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)
    results = {"u1": 1, "u2": TimeoutException(), "u3": TimeoutException(), "u4": TimeoutException(), "u5": 1}
    check_kwargs = ({"side_effect": login_state} if isinstance(login_state, Exception)
                    else {"return_value": login_state})

    with patch.object(scraper, "scrape_user_post", side_effect=make_scrape_side_effect(results)) as mock_scrape, \
         patch.object(scraper, "is_logged_in", **check_kwargs) as mock_check:
        with pytest.raises(Exception) as exc_info:
            scraper.scrape_target_users(list(results), "20261115")

    assert expected_tag in str(exc_info.value)
    # 4번째 사용자에서 연속 3명 실패 -> 5번째 사용자는 시도하지 않는다.
    assert [c.args[0] for c in mock_scrape.call_args_list] == ["u1", "u2", "u3", "u4"]
    mock_check.assert_called_once()
    assert any(expected_tag in m for m in messages(caplog, logging.ERROR))
    assert any(m.startswith("수집 결과:") and "중단으로 미수집 1명" in m
               for m in messages(caplog, logging.WARNING))


def test_scrape_target_users_resets_failure_count_on_success(scraper, monkeypatch):
    """성공한 사용자(게시글 0건 포함)가 나오면 연속 실패 횟수는 0으로 돌아간다."""
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)
    results = {"u1": TimeoutException(), "u2": TimeoutException(), "u3": 0,
               "u4": TimeoutException(), "u5": TimeoutException(), "u6": 5}

    with patch.object(scraper, "scrape_user_post", side_effect=make_scrape_side_effect(results)), \
         patch.object(scraper, "is_logged_in") as mock_check:
        total = scraper.scrape_target_users(list(results), "20261115")

    assert total == 5
    mock_check.assert_not_called()


@pytest.mark.parametrize("login_state, expected_tag", [
    (False, "[쿠키 로그인 실패]"),
    (True, "[수집 게시글 0건]"),
])
def test_scrape_target_users_fails_when_no_posts_collected(scraper, caplog, login_state, expected_tag):
    """대상이 2명 이상인데 게시글 합계가 0건이면 로그인 상태를 재확인해 실패로 처리한다."""
    with patch.object(scraper, "scrape_user_post", return_value=0), \
         patch.object(scraper, "is_logged_in", return_value=login_state) as mock_check:
        with pytest.raises(Exception) as exc_info:
            scraper.scrape_target_users(["u1", "u2"], "20261115")

    assert expected_tag in str(exc_info.value)
    mock_check.assert_called_once()


def test_scrape_target_users_single_target_with_zero_posts_is_ok(scraper):
    """대상 1명이 정상 수집됐는데 게시글이 0건이면 실패가 아니다."""
    with patch.object(scraper, "scrape_user_post", return_value=0), \
         patch.object(scraper, "is_logged_in") as mock_check:
        assert scraper.scrape_target_users(["u1"], "20261115") == 0

    mock_check.assert_not_called()


@pytest.mark.parametrize("login_state, expected_tag", [
    (False, "[쿠키 로그인 실패]"),
    (True, "[수집 전원 실패]"),
])
def test_scrape_target_users_fails_when_single_target_fails(scraper, monkeypatch, caplog, login_state, expected_tag):
    """대상 1명이 예외로 실패하면(성공한 사용자 0명) 로그인 상태를 재확인해 실패로 처리한다."""
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)

    with patch.object(scraper, "scrape_user_post", side_effect=TimeoutException("profile timeout")), \
         patch.object(scraper, "upload_posts_json_to_gcs") as mock_upload, \
         patch.object(scraper, "is_logged_in", return_value=login_state) as mock_check:
        with pytest.raises(Exception) as exc_info:
            scraper.scrape_target_users(["u1"], "20261115", posts_json_upload=True)

    assert expected_tag in str(exc_info.value)
    mock_check.assert_called_once()
    mock_upload.assert_not_called()
    assert any(expected_tag in m for m in messages(caplog, logging.ERROR))


def test_scrape_target_users_all_failed_takes_precedence_over_zero_posts(scraper, monkeypatch, caplog):
    """대상 2명이 모두 예외로 실패하면(연속 실패 한도 미만) 전원 실패로 한 번만 중단한다."""
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)

    with patch.object(scraper, "scrape_user_post", side_effect=TimeoutException("profile timeout")) as mock_scrape, \
         patch.object(scraper, "is_logged_in", return_value=True) as mock_check:
        with pytest.raises(Exception) as exc_info:
            scraper.scrape_target_users(["u1", "u2"], "20261115")

    assert "[수집 전원 실패]" in str(exc_info.value)
    assert mock_scrape.call_count == 2
    # 재확인(= _abort_scraping)은 한 번만 일어나고, 연속 실패·게시글 0건 태그는 쓰지 않는다.
    mock_check.assert_called_once()
    errors = messages(caplog, logging.ERROR)
    assert sum("[수집 전원 실패]" in m for m in errors) == 1
    assert not any("[수집 게시글 0건]" in m or "[수집 연속 실패]" in m for m in errors)


# --- main() 연동 ---

TODAY_YMD = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")


@pytest.fixture
def main_env(tmp_path, monkeypatch):
    """main() 테스트용: 가짜 설정 파일과 가짜 웹드라이버를 준비한다."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"TWEET_USERNAME": "fake-user",
                                       "TWEET_PASSWORD": "fake-password",
                                       "VERIFICATION_INFO": "fake-info"}), encoding="utf-8")
    monkeypatch.setattr(tsp, "CONFIG_FILE", str(config_path))

    fake_driver = MagicMock()

    def fake_set_webdriver(self):
        self.driver = fake_driver
        self.wait = MagicMock()
        self.actions = MagicMock()

    monkeypatch.setattr(tsp.TweetScraper, "set_webdriver", fake_set_webdriver)
    return fake_driver


def test_main_exits_when_login_fails(main_env):
    """login_with_cookies() 가 False 면 수집 없이 sys.exit(1) 하고, 저장한 화면 소스 업로드를 시도한다."""
    with patch.object(tsp.TweetScraper, "login_with_cookies", return_value=False), \
         patch.object(tsp.TweetScraper, "upload_login_error_page_to_gcs") as mock_upload_page, \
         patch.object(tsp.TweetScraper, "scrape_user_post") as mock_scrape:
        with pytest.raises(SystemExit) as exc_info:
            tsp.main(TODAY_YMD, posts_json_upload=True, tweet_usernames=["u1", "u2"])

    assert exc_info.value.code == 1
    mock_upload_page.assert_called_once_with(TODAY_YMD)
    mock_scrape.assert_not_called()
    main_env.quit.assert_called_once()


def test_main_does_not_upload_error_page_when_upload_disabled(main_env):
    with patch.object(tsp.TweetScraper, "login_with_cookies", return_value=True), \
         patch.object(tsp.TweetScraper, "upload_login_error_page_to_gcs") as mock_upload_page, \
         patch.object(tsp.TweetScraper, "scrape_user_post", return_value=1):
        tsp.main(TODAY_YMD, posts_json_upload=False, tweet_usernames=["u1", "u2"])

    mock_upload_page.assert_not_called()


def test_main_list_mode_skips_failed_user(main_env):
    results = {"u1": TimeoutException("profile timeout"), "u2": 2, "u3": 0}

    with patch.object(tsp.TweetScraper, "login_with_cookies", return_value=True), \
         patch.object(tsp.TweetScraper, "upload_login_error_page_to_gcs"), \
         patch.object(tsp.TweetScraper, "scrape_user_post", side_effect=make_scrape_side_effect(results)), \
         patch.object(tsp.TweetScraper, "upload_posts_json_to_gcs") as mock_upload:
        tsp.main(TODAY_YMD, posts_json_upload=True, tweet_usernames=list(results))  # sys.exit 하지 않아야 한다

    assert [c.args[0] for c in mock_upload.call_args_list] == ["u2", "u3"]
    main_env.quit.assert_called_once()


def test_main_exits_after_consecutive_scrape_failures(main_env, monkeypatch):
    monkeypatch.setattr(tsp, "MAX_CONSECUTIVE_SCRAPE_FAILURES", 3)

    with patch.object(tsp.TweetScraper, "login_with_cookies", return_value=True), \
         patch.object(tsp.TweetScraper, "scrape_user_post", side_effect=TimeoutException("timeout")) as mock_scrape, \
         patch.object(tsp.TweetScraper, "is_logged_in", return_value=False) as mock_check:
        with pytest.raises(SystemExit) as exc_info:
            tsp.main(TODAY_YMD, posts_json_upload=False, tweet_usernames=["u1", "u2", "u3", "u4"])

    assert exc_info.value.code == 1
    assert mock_scrape.call_count == 3
    mock_check.assert_called_once()
    main_env.quit.assert_called_once()


def test_main_single_user_mode_fails_immediately(main_env):
    """단일 사용자 모드는 예외가 나면 재확인 없이 바로 실패한다."""
    with patch.object(tsp.TweetScraper, "login_with_cookies", return_value=True), \
         patch.object(tsp.TweetScraper, "scrape_user_post", side_effect=TimeoutException("timeout")), \
         patch.object(tsp.TweetScraper, "is_logged_in") as mock_check:
        with pytest.raises(SystemExit) as exc_info:
            tsp.main(TODAY_YMD, posts_json_upload=False, tweet_username="u1")

    assert exc_info.value.code == 1
    mock_check.assert_not_called()


def test_main_single_user_mode_zero_posts_is_ok(main_env):
    with patch.object(tsp.TweetScraper, "login_with_cookies", return_value=True), \
         patch.object(tsp.TweetScraper, "scrape_user_post", return_value=0), \
         patch.object(tsp.TweetScraper, "upload_posts_json_to_gcs") as mock_upload:
        tsp.main(TODAY_YMD, posts_json_upload=True, tweet_username="u1")  # sys.exit 하지 않아야 한다

    mock_upload.assert_called_once_with("u1", TODAY_YMD)


# --- 쿠키 로그인 실패 CRITICAL 로그 (경로별 구분 / main 연동) ---

def test_login_with_cookies_unexpected_error_logs_error_with_traceback_and_critical(scraper, login_env, caplog):
    """경로 2: traceback 포함 ERROR 와 CRITICAL 이 각각 1건 남고, CRITICAL 에는 예외 메시지(쿠키 값)가 없다."""
    scraper.driver.add_cookie.side_effect = Exception(f"bad cookie {FAKE_AUTH_TOKEN_VALUE} {FAKE_CT0_VALUE}")

    assert scraper.login_with_cookies() is False

    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "예상치 못한 오류" in r.getMessage()]
    assert len(errors) == 1 and errors[0].exc_info is not None
    crit = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crit) == 1
    assert crit[0].exc_info is None
    text = crit[0].getMessage()
    assert text.startswith(CRITICAL_PREFIX)
    for value in FAKE_COOKIE_VALUES:
        assert value not in text
    assert "bad cookie" not in text


def test_login_with_cookies_critical_reasons_are_distinguishable(scraper, login_env, monkeypatch, caplog):
    """세 실패 경로의 CRITICAL 문구는 공통 접두어로 시작하지만 사유 부분이 서로 다르다."""
    collected = {}

    # 경로 1: 쿠키 파일 없음
    monkeypatch.setattr(tsp, "COOKIE_FILE", str(login_env / "no_such_cookies.json"))
    caplog.clear()
    assert scraper.login_with_cookies() is False
    collected["file"] = messages(caplog, logging.CRITICAL)

    # 경로 2: 쿠키 적용 중 예외
    monkeypatch.setattr(tsp, "COOKIE_FILE", str(login_env / "tweet_cookies.json"))
    scraper.driver.add_cookie.side_effect = Exception("boom")
    caplog.clear()
    assert scraper.login_with_cookies() is False
    collected["apply"] = messages(caplog, logging.CRITICAL)
    scraper.driver.add_cookie.side_effect = None

    # 경로 3: 강제 모드에서 로그인 확인 실패
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", True)
    caplog.clear()
    with patch.object(scraper, "is_logged_in", return_value=False):
        assert scraper.login_with_cookies() is False
    collected["enforce"] = messages(caplog, logging.CRITICAL)

    assert all(len(v) == 1 and v[0].startswith(CRITICAL_PREFIX) for v in collected.values())
    texts = {k: v[0] for k, v in collected.items()}
    assert len(set(texts.values())) == 3
    assert "쿠키 파일 오류" in texts["file"]
    assert "쿠키 적용 중 오류" in texts["apply"]
    assert "로그인 상태 확인 실패" in texts["enforce"] and "강제 모드" in texts["enforce"]


def test_login_with_cookies_observe_mode_has_warning_but_no_critical(scraper, login_env, monkeypatch, caplog):
    """관찰 모드(True 반환): WARNING 은 남고 CRITICAL 은 없다."""
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", False)
    with patch.object(scraper, "is_logged_in", return_value=False):
        assert scraper.login_with_cookies() is True
    assert any("관찰 모드" in m for m in messages(caplog, logging.WARNING))
    assert not messages(caplog, logging.CRITICAL)


@pytest.mark.parametrize("scenario", ["cookie_file_missing", "apply_error", "enforce_check_failed"])
def test_main_logs_critical_and_exits_when_cookie_login_fails(main_env, tmp_path, monkeypatch, caplog, scenario):
    """실제 login_with_cookies 를 거쳐 main() 을 호출해도 CRITICAL 1건이 남고 sys.exit(1) 로 끝난다."""
    cookie_path = tmp_path / "tweet_cookies.json"
    cookie_path.write_text(json.dumps(make_fake_cookies(dt.datetime.now(dt.timezone.utc))), encoding="utf-8")
    monkeypatch.setattr(tsp, "COOKIE_FILE", str(cookie_path))
    monkeypatch.setattr(tsp, "pjt_home_path", str(tmp_path))
    monkeypatch.setattr(tsp, "ENFORCE_LOGIN_CHECK", scenario == "enforce_check_failed")
    main_env.page_source = "<html>logged out</html>"
    if scenario == "cookie_file_missing":
        monkeypatch.setattr(tsp, "COOKIE_FILE", str(tmp_path / "no_such_cookies.json"))
    elif scenario == "apply_error":
        main_env.add_cookie.side_effect = Exception("boom")

    with patch('src.services.tweet_scrapper_post.time.sleep'), \
         patch.object(tsp.TweetScraper, "is_logged_in", return_value=False), \
         patch.object(tsp.TweetScraper, "upload_login_error_page_to_gcs"), \
         patch.object(tsp.TweetScraper, "scrape_user_post") as mock_scrape:
        with pytest.raises(SystemExit) as exc_info:
            tsp.main(TODAY_YMD, posts_json_upload=True, tweet_usernames=["u1"])

    assert exc_info.value.code == 1
    mock_scrape.assert_not_called()
    crit = messages(caplog, logging.CRITICAL)
    assert len(crit) == 1 and crit[0].startswith(CRITICAL_PREFIX)
    assert_no_cookie_value_logged(caplog)
