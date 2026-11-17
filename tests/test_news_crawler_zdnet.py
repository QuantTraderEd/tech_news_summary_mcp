import os
import site
import pytest
import requests
import datetime as dt

from unittest.mock import patch, MagicMock, mock_open

import pytz

# Add project root to the Python path
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services.news_crawler_zdnet import NewsCrawler_ZDNet, PARSE_FAILED_DATETIME, MAX_REQUEST_ATTEMPTS, main as zdnet_main

# Constants
BASE_URL = "https://zdnet.co.kr/news/?lstcode=0050"
KST = pytz.timezone('Asia/Seoul')

# --- Fixtures ---

@pytest.fixture
def mock_user_agent(mocker):
    """Fixture to mock UserAgent."""
    mock = mocker.patch('src.services.news_crawler_zdnet.UserAgent')
    mock.return_value.random = 'test-user-agent'
    return mock

@pytest.fixture(autouse=True)
def mock_sleep(mocker):
    """재시도 대기(time.sleep)로 테스트가 느려지지 않도록 목 처리한다."""
    return mocker.patch('src.services.news_crawler_zdnet.time.sleep')

@pytest.fixture
def crawler(mock_user_agent):
    """Fixture to create a NewsCrawler_ZDNet instance."""
    return NewsCrawler_ZDNet(base_url=BASE_URL)

# --- Test Cases for NewsCrawler_ZDNet Class ---

def test_init(crawler):
    """Test crawler initialization."""
    assert crawler.base_url == BASE_URL
    assert "User-Agent" in crawler.headers
    assert crawler.headers['User-Agent'] == 'test-user-agent'
    assert isinstance(crawler.start_date, dt.datetime)
    assert isinstance(crawler.end_date, dt.datetime)

def test_set_target_date_range(crawler):
    """Test setting the target date range."""
    start = KST.localize(dt.datetime(2024, 1, 1))
    end = KST.localize(dt.datetime(2024, 1, 31))
    crawler.set_target_date_range(start, end)
    assert crawler.start_date == start
    assert crawler.end_date == end

def test_parse_date(crawler):
    """Test parsing of ZDNet's specific date format."""
    valid_date_str = "2025.05.23 AM 11:22"
    expected_dt = dt.datetime(2025, 5, 23, 11, 22)
    assert crawler._parse_date(valid_date_str) == expected_dt

    invalid_date_str = "invalid-date"
    assert crawler._parse_date(invalid_date_str) == PARSE_FAILED_DATETIME

def test_parse_date_from_link(crawler):
    """Test parsing date from an article link."""
    valid_link = "/news/article.html?no=20250628100000"
    expected_dt = dt.datetime(2025, 6, 28, 10, 0, 0)
    assert crawler._parse_date_from_link(valid_link) == expected_dt

    invalid_link = "/news/article.html?id=123"
    assert crawler._parse_date_from_link(invalid_link) == PARSE_FAILED_DATETIME

    # no= 값이 14자리 타임스탬프가 아닌 경우에도 예외 없이 실패 값을 반환해야 한다.
    malformed_link = "/news/article.html?no=12345"
    assert crawler._parse_date_from_link(malformed_link) == PARSE_FAILED_DATETIME

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_articles(mock_get, crawler):
    """Test fetching and filtering articles from a list page."""
    # Mock HTML for the article list page
    mock_html = """
    <html><body>
        <div class="newsPost">
            <a href="/news/article.html?no=20250628100000"></a>
            <div class="assetText"><h3>Article In Range</h3></div>
            <p class="byline"><span>2025.06.28 AM 10:00</span></p>
        </div>
        <div class="newsPost">
            <a href="/news/article.html?no=20230101120000"></a>
            <div class="assetText"><h3>Article Out of Range</h3></div>
            <p class="byline"><span>2023.01.01 PM 12:00</span></p>
        </div>
        <div class="sub_news">
            <a href="/news/article.html?no=20250628110000">Sub News In Range</a>
        </div>
        <div class="top_news">
            <a href="/news/article.html?no=20250628120000">Top News In Range</a>
        </div>
    </body></html>
    """
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    # Set a date range that includes the test articles
    start_date = KST.localize(dt.datetime(2025, 6, 27))
    end_date = KST.localize(dt.datetime(2025, 6, 29))
    crawler.set_target_date_range(start_date, end_date)

    articles = crawler.fetch_articles()

    mock_get.assert_called_once_with(BASE_URL, headers=crawler.headers, timeout=10)
    assert len(articles) == 3
    assert articles[0]['title'] == "Article In Range"
    assert articles[0]['published_date'] == "2025-06-28"
    assert articles[1]['title'] == "Sub News In Range"
    assert articles[2]['title'] == "Top News In Range"
    assert all('https://zdnet.co.kr' in a['url'] for a in articles)

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_articles_skips_unparsable_dates(mock_get, crawler):
    """날짜를 파싱할 수 없는 기사는 예외 없이 건너뛰고 나머지 기사는 수집되어야 한다."""
    mock_html = """
    <html><body>
        <div class="newsPost">
            <a href="/news/article.html?no=20250628100000"></a>
            <div class="assetText"><h3>Broken Date Article</h3></div>
            <p class="byline"><span>날짜아님</span></p>
        </div>
        <div class="newsPost">
            <a href="/news/article.html?no=20250628100000"></a>
            <div class="assetText"><h3>Article In Range</h3></div>
            <p class="byline"><span>2025.06.28 AM 10:00</span></p>
        </div>
        <div class="sub_news">
            <a href="/news/article.html?id=123">Sub News Without Timestamp</a>
        </div>
    </body></html>
    """
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    crawler.set_target_date_range(KST.localize(dt.datetime(2025, 6, 27)),
                                  KST.localize(dt.datetime(2025, 6, 29)))

    articles = crawler.fetch_articles()

    assert [a['title'] for a in articles] == ["Article In Range"]

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_article_content(mock_get, crawler):
    """Test fetching and cleaning individual article content."""
    mock_html = """
    <html><body>
        <div id="articleBody">
            <h1>Main Title</h1>
            <p>This is the first paragraph.</p>
            <script>console.log("removed");</script>
            <p>This is the second paragraph.</p>
            <h2>관련기사</h2>
            <p>This related content should be removed.</p>
        </div>
    </body></html>
    """
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    content = crawler.fetch_article_content("http://fake.url/article")

    mock_get.assert_called_once_with("http://fake.url/article", headers=crawler.headers, timeout=10)
    assert "Main Title" in content
    assert "This is the first paragraph." in content
    assert "This is the second paragraph." in content
    assert "removed" not in content
    assert "관련기사" not in content

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_article_content_not_found(mock_get, crawler):
    """Test handling for when article content container is not found."""
    mock_html = "<html><body><p>No article body here.</p></body></html>"
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    content = crawler.fetch_article_content("http://fake.url/article")
    assert content == "기사 내용을 찾을 수 없습니다."

REQUEST_ERROR_MESSAGE = "요청 오류로 기사 내용을 찾을 수 없습니다."
TIMEOUT_MESSAGE = "타임아웃으로 기사 내용을 찾을 수 없습니다."

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_article_content_http_error(mock_get, crawler):
    """HTTPError(404/5xx)가 나도 예외 없이 고정 문구를 반환하고, 예외 메시지는 반환값에 섞이지 않아야 한다."""
    url = "http://fake.url/article"
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
        f"404 Client Error: Not Found for url: {url}")
    mock_get.return_value = mock_response

    content = crawler.fetch_article_content(url)

    mock_get.assert_called_once_with(url, headers=crawler.headers, timeout=10)
    assert content == REQUEST_ERROR_MESSAGE

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_article_content_connection_error(mock_get, crawler):
    """ConnectionError 가 나도 예외 없이 고정 문구를 반환하고, 예외 메시지는 반환값에 섞이지 않아야 한다."""
    url = "http://fake.url/article"
    mock_get.side_effect = requests.exceptions.ConnectionError(
        f"HTTPSConnectionPool(host='fake.url'): Max retries exceeded with url: {url}")

    content = crawler.fetch_article_content(url)

    assert content == REQUEST_ERROR_MESSAGE

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_article_content_timeout_keeps_timeout_message(mock_get, crawler):
    """ReadTimeout 은 새 RequestException 분기에 가려지지 않고 기존 타임아웃 문구를 반환해야 한다."""
    mock_get.side_effect = requests.exceptions.ReadTimeout("read timed out")

    content = crawler.fetch_article_content("http://fake.url/article")

    assert content == TIMEOUT_MESSAGE
    assert content != REQUEST_ERROR_MESSAGE

# --- Test Cases for retry on timeout ---

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_articles_retries_after_timeout(mock_get, crawler, mock_sleep):
    """목록 페이지 요청이 타임아웃 나면 재시도하고, 재시도가 성공하면 정상적으로 기사를 수집해야 한다."""
    mock_response = MagicMock()
    mock_response.text = """
    <html><body>
        <div class="newsPost">
            <a href="/news/view.html?no=20250628100000"></a>
            <div class="assetText"><h3>Article In Range</h3></div>
            <p class="byline"><span>2025.06.28 AM 10:00</span></p>
        </div>
    </body></html>
    """
    mock_get.side_effect = [requests.exceptions.ReadTimeout("read timed out"), mock_response]
    crawler.set_target_date_range(KST.localize(dt.datetime(2025, 6, 27)),
                                  KST.localize(dt.datetime(2025, 6, 29)))

    articles = crawler.fetch_articles()

    assert mock_get.call_count == 2
    mock_sleep.assert_called_once()
    assert [a['title'] for a in articles] == ["Article In Range"]

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_articles_skips_when_all_retries_time_out(mock_get, crawler):
    """재시도까지 모두 타임아웃이면 예외 없이 빈 목록을 반환해 해당 섹션을 건너뛰어야 한다."""
    mock_get.side_effect = requests.exceptions.ReadTimeout("read timed out")

    articles = crawler.fetch_articles()

    assert articles == []
    assert mock_get.call_count == MAX_REQUEST_ATTEMPTS

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_articles_does_not_retry_http_error(mock_get, crawler):
    """HTTP 오류(4xx/5xx)는 재시도하지 않고 바로 건너뛰어야 한다."""
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Client Error")
    mock_get.return_value = mock_response

    articles = crawler.fetch_articles()

    assert articles == []
    mock_get.assert_called_once()

@patch('src.services.news_crawler_zdnet.requests.get')
def test_fetch_article_content_retries_after_timeout(mock_get, crawler):
    """본문 요청이 타임아웃 나도 재시도가 성공하면 본문을 반환해야 한다."""
    mock_response = MagicMock()
    mock_response.text = '<html><body><div id="articleBody"><p>Body text.</p></div></body></html>'
    mock_get.side_effect = [requests.exceptions.ConnectTimeout("connect timed out"), mock_response]

    content = crawler.fetch_article_content("http://fake.url/article")

    assert mock_get.call_count == 2
    assert content == "Body text."

@patch('src.services.news_crawler_zdnet.json.dump')
@patch('src.services.news_crawler_zdnet.open', new_callable=mock_open)
@patch('src.services.news_crawler_zdnet.requests.get')
def test_main_does_not_exit_when_list_page_times_out(mock_get, mock_file_open, mock_json_dump, mock_user_agent):
    """목록 페이지가 계속 타임아웃 나도 main 은 sys.exit 하지 않고 빈 목록을 저장해야 한다."""
    mock_get.side_effect = requests.exceptions.ReadTimeout("read timed out")

    zdnet_main(target_section="반도체", base_ymd="20250628")  # SystemExit 이 나면 테스트 실패

    mock_json_dump.assert_called_once()
    assert mock_json_dump.call_args.args[0] == []

# --- Test Cases for main Function ---

@patch('src.services.news_crawler_zdnet.NewsCrawler_ZDNet')
@patch('src.services.news_crawler_zdnet.open', new_callable=mock_open)
@patch('src.services.news_crawler_zdnet.json.dump')
def test_main_success(mock_json_dump, mock_file_open, MockCrawler, mock_user_agent):
    """Test the main function's success path."""
    # Mock crawler instance and its methods
    mock_crawler_instance = MagicMock()
    mock_crawler_instance.fetch_articles.return_value = [
        {'title': 'Test Article', 'url': 'http://fake.url', 'published_date': '2024-01-01', 'content': ''}
    ]
    mock_crawler_instance.fetch_article_content.return_value = "Full article content."
    MockCrawler.return_value = mock_crawler_instance

    # Run the main function
    zdnet_main(target_section="반도체", base_ymd="20240101")

    # Assertions
    MockCrawler.assert_called_once_with("https://zdnet.co.kr/news/?lstcode=0050")
    mock_crawler_instance.set_target_date_range.assert_called_once()
    mock_crawler_instance.fetch_articles.assert_called_once()
    mock_crawler_instance.fetch_article_content.assert_called_once_with('http://fake.url')

    # Check file writing
    expected_filepath = os.path.join(pjt_home_path, 'data/zdnet_semiconductor_articles.json')
    mock_file_open.assert_called_once_with(expected_filepath, 'w', encoding='utf-8')
    
    # Check that json.dump was called with the updated content
    expected_data = [{'title': 'Test Article', 'url': 'http://fake.url', 'published_date': '2024-01-01', 'content': 'Full article content.'}]
    mock_json_dump.assert_called_once_with(expected_data, mock_file_open(), ensure_ascii=False, indent=2)

@patch('src.services.news_crawler_zdnet.NewsCrawler_ZDNet')
@patch('sys.exit')
def test_main_exception(mock_exit, MockCrawler, mock_user_agent):
    """Test the main function's exception handling."""
    # Configure the mock crawler's method to raise an exception
    mock_crawler_instance = MagicMock()
    mock_crawler_instance.fetch_articles.side_effect = Exception("Test exception")
    MockCrawler.return_value = mock_crawler_instance

    # Run main and assert that sys.exit(1) is called
    zdnet_main(target_section="반도체", base_ymd="20240101")
    mock_exit.assert_called_once_with(1)

@patch('src.services.news_crawler_zdnet.json.dump')
@patch('src.services.news_crawler_zdnet.open', new_callable=mock_open)
@patch('src.services.news_crawler_zdnet.requests.get')
def test_main_continues_when_one_article_fetch_fails(mock_get, mock_file_open, mock_json_dump, mock_user_agent):
    """기사 한 건의 본문 요청이 HTTPError 여도 main 은 종료하지 않고 두 기사를 모두 저장해야 한다.

    크롤러 클래스는 목 처리하지 않아 fetch_article_content 가 실제로 실행된다.
    requests.get 과 파일 쓰기(open, json.dump)만 목 처리한다.
    """
    list_html = """
    <html><body>
        <div class="newsPost">
            <a href="/news/view.html?no=20250628100000"></a>
            <div class="assetText"><h3>Failing Article</h3></div>
            <p class="byline"><span>2025.06.28 AM 10:00</span></p>
        </div>
        <div class="newsPost">
            <a href="/news/view.html?no=20250628110000"></a>
            <div class="assetText"><h3>Good Article</h3></div>
            <p class="byline"><span>2025.06.28 AM 11:00</span></p>
        </div>
    </body></html>
    """
    failing_url = "https://zdnet.co.kr/news/view.html?no=20250628100000"
    good_url = "https://zdnet.co.kr/news/view.html?no=20250628110000"

    def fake_get(url, **kwargs):
        response = MagicMock()
        if url == BASE_URL:
            response.text = list_html
        elif url == failing_url:
            response.raise_for_status.side_effect = requests.exceptions.HTTPError(
                f"500 Server Error: Internal Server Error for url: {url}")
        elif url == good_url:
            response.text = '<html><body><div id="articleBody"><p>Good body text.</p></div></body></html>'
        else:
            raise AssertionError(f"unexpected url: {url}")
        return response

    mock_get.side_effect = fake_get

    zdnet_main(target_section="반도체", base_ymd="20250628")  # SystemExit 이 나면 테스트 실패

    expected_filepath = os.path.join(pjt_home_path, 'data/zdnet_semiconductor_articles.json')
    mock_file_open.assert_called_once_with(expected_filepath, 'w', encoding='utf-8')
    mock_json_dump.assert_called_once()
    saved_articles = mock_json_dump.call_args.args[0]
    assert [a['url'] for a in saved_articles] == [failing_url, good_url]
    assert saved_articles[0]['content'] == REQUEST_ERROR_MESSAGE
    assert saved_articles[1]['content'] == "Good body text."
