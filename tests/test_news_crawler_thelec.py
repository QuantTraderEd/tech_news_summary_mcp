import os
import sys
import json
import re
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

from app.services.news_crawler_thelec import ThelecNewsCrawler, main as thelec_main

# Constants
BASE_URL = "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N2"
TARGET_SECTION = "반도체"
KST = pytz.timezone('Asia/Seoul')

# --- Fixtures ---

@pytest.fixture
def mock_user_agent(mocker):
    """Fixture to mock UserAgent."""
    mock = mocker.patch('app.services.news_crawler_thelec.UserAgent')
    mock.return_value.random = 'test-user-agent'
    return mock

@pytest.fixture
def crawler(mock_user_agent):
    """Fixture to create a ThelecNewsCrawler instance."""
    return ThelecNewsCrawler(base_url=BASE_URL, target_section=TARGET_SECTION)

# --- Test Cases for ThelecNewsCrawler Class ---

def test_init(crawler):
    """Test crawler initialization."""
    assert crawler.base_url == BASE_URL
    assert crawler.target_section == TARGET_SECTION
    assert "User-Agent" in crawler.headers
    assert isinstance(crawler.start_date, dt.datetime)

def test_parse_date(crawler):
    """Test parsing of TheLec's specific date formats."""
    assert crawler._parse_date("2025-05-29 08:52") == dt.datetime(2025, 5, 29, 8, 52)
    assert crawler._parse_date("2025-05-30") == dt.datetime(2025, 5, 30)
    assert crawler._parse_date("invalid-date") == dt.datetime.min

def test_is_target_section(crawler):
    """Test the logic for identifying if an article belongs to the target section."""
    # Case 1: Section tag matches
    html_with_section = '<div class="list-item"><small class="list-section">반도체</small><h3>Title</h3></div>'
    element_with_section = BeautifulSoup(html_with_section, 'html.parser').div
    assert crawler._is_target_section(element_with_section) is True

    # Case 2: Section tag does not match
    html_no_section_match = '<div class="list-item"><small class="list-section">디스플레이</small><h3>Title</h3></div>'
    element_no_section_match = BeautifulSoup(html_no_section_match, 'html.parser').div
    assert crawler._is_target_section(element_no_section_match) is False

    # Case 3: Section tag is missing
    html_no_section_tag = '<div class="list-item"><h3>Title</h3></div>'
    element_no_section_tag = BeautifulSoup(html_no_section_tag, 'html.parser').div
    assert crawler._is_target_section(element_no_section_tag) is False

@patch('app.services.news_crawler_thelec.requests.get')
def test_get_published_date_from_article_page(mock_get, crawler):
    """Test extracting the published date from a single article page."""
    mock_html = """
    <html><body>
        <div class="article-view-info">
            <span>승인 2025.06.28 10:30</span>
        </div>
    </body></html>
    """
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    expected_date = dt.datetime(2025, 6, 28, 10, 30)
    result_date = crawler._get_published_date_from_article_page("http://fake.url/article")
    
    assert result_date == expected_date

@patch('app.services.news_crawler_thelec.requests.get')
def test_get_section_from_article_page(mock_get, crawler):
    """Test extracting the section from a single article page's meta tag."""
    mock_html = """
    <html><head>
        <meta property="article:section" content="반도체"/>
    </head></html>
    """
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    section = crawler._get_section_from_article_page("http://fake.url/article")
    assert section == "반도체"

@patch('app.services.news_crawler_thelec.requests.get')
def test_fetch_articles_integration(mock_get, crawler):
    """
    Test the fetch_articles method, integrating the section and date filtering logic.
    This test mocks the network calls made by helper methods.
    """
    # Mock for the main list page
    list_page_html = """
    <html><body>
        <div class="list-item">
            <a href="/news/articleView.html?idxno=1">Article 1 (Semiconductor, In Date)</a>
            <small class="list-section">반도체</small>
            <span class="by-time">2025-06-28 10:00</span>
        </div>
        <div class="list-item">
            <a href="/news/articleView.html?idxno=2">Article 2 (Display, In Date)</a>
            <small class="list-section">디스플레이</small>
            <span class="by-time">2025-06-28 11:00</span>
        </div>
        <div class="list-item">
            <a href="/news/articleView.html?idxno=3">Article 3 (Semiconductor, Out of Date)</a>
            <small class="list-section">반도체</small>
            <span class="by-time">2023-01-01 12:00</span>
        </div>
    </body></html>
    """
    mock_list_response = MagicMock()
    mock_list_response.text = list_page_html
    mock_list_response.raise_for_status.return_value = None
    
    # The first call to get() is for the list page.
    mock_get.return_value = mock_list_response

    # Set a date range that includes only the first article
    start_date = KST.localize(dt.datetime(2025, 6, 27))
    end_date = KST.localize(dt.datetime(2025, 6, 29))
    crawler.set_target_date_range(start_date, end_date)

    articles = crawler.fetch_articles(pages=1)

    assert len(articles) == 1
    assert articles[0]['title'] == "Article 1 (Semiconductor, In Date)"
    assert articles[0]['url'] == "https://www.thelec.kr/news/articleView.html?idxno=1"

@patch('app.services.news_crawler_thelec.requests.get')
def test_fetch_article_content(mock_get, crawler):
    """Test fetching and cleaning individual article content."""
    mock_html = """
    <html><body>
        <div class="article-content">
            <p>Main content here.</p>
            <script>ads.js</script>
            <div class="ad_class">Advertisement</div>
            <h3>관련기사</h3>
            <p>Related article link.</p>
        </div>
    </body></html>
    """
    mock_response = MagicMock()
    mock_response.text = mock_html
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    content = crawler.fetch_article_content("http://fake.url/article")

    assert "Main content here" in content
    assert "ads.js" not in content
    assert "Advertisement" not in content
    assert "관련기사" not in content

# --- Test Cases for main Function ---

@patch('app.services.news_crawler_thelec.ThelecNewsCrawler')
@patch('app.services.news_crawler_thelec.open', new_callable=mock_open)
@patch('app.services.news_crawler_thelec.json.dump')
def test_main_success(mock_json_dump, mock_file_open, MockCrawler, mock_user_agent):
    """Test the main function's success path."""
    mock_crawler_instance = MagicMock()
    mock_crawler_instance.fetch_articles.return_value = [
        {'title': 'Test Article', 'url': 'http://fake.url', 'published_date': '2024-01-01', 'content': ''}
    ]
    mock_crawler_instance.fetch_article_content.return_value = "Full article content."
    MockCrawler.return_value = mock_crawler_instance

    thelec_main(target_section="반도체", base_ymd="20240101")

    MockCrawler.assert_called_once_with(
        "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N2&view_type=sm",
        target_section="반도체"
    )
    mock_crawler_instance.set_target_date_range.assert_called_once()
    mock_crawler_instance.fetch_articles.assert_called_once_with(pages=2)
    mock_crawler_instance.fetch_article_content.assert_called_once_with('http://fake.url')

    expected_filepath = os.path.join(pjt_home_path, 'data/thelec_semiconductor_articles.json')
    mock_file_open.assert_called_once_with(expected_filepath, 'w', encoding='utf-8')
    
    expected_data = [{'title': 'Test Article', 'url': 'http://fake.url', 'published_date': '2024-01-01', 'content': 'Full article content.'}]
    mock_json_dump.assert_called_once_with(expected_data, mock_file_open(), ensure_ascii=False, indent=2)

@patch('app.services.news_crawler_thelec.ThelecNewsCrawler')
@patch('sys.exit')
def test_main_exception(mock_exit, MockCrawler, mock_user_agent):
    """Test the main function's exception handling."""
    mock_crawler_instance = MagicMock()
    mock_crawler_instance.fetch_articles.side_effect = Exception("Test exception")
    MockCrawler.return_value = mock_crawler_instance

    thelec_main(target_section="반도체", base_ymd="20240101")
    mock_exit.assert_called_once_with(1)
