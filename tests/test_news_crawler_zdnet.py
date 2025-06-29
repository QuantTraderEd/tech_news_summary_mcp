import os
import sys
import site
import json
import pytest
import datetime as dt

from unittest.mock import patch, MagicMock, mock_open

import pytz
from bs4 import BeautifulSoup

# Add project root to the Python path
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services.news_crawler_zdnet import NewsCrawler_ZDNet, main as zdnet_main

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
    assert crawler._parse_date(invalid_date_str) == dt.datetime.min

def test_parse_date_from_link(crawler):
    """Test parsing date from an article link."""
    valid_link = "/news/article.html?no=20250628100000"
    expected_dt = dt.datetime(2025, 6, 28, 10, 0, 0)
    assert crawler._parse_date_from_link(valid_link) == expected_dt

    invalid_link = "/news/article.html?id=123"
    assert crawler._parse_date_from_link(invalid_link) == dt.datetime.min

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
