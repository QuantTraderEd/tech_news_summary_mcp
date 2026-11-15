import os
import site
import datetime as dt

from unittest.mock import patch

import pytest
import pytz

# Add project root to the Python path
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services.news_crawler_etnews import NewsCrawlerEtnews, PARSE_FAILED_DATETIME

# Constants
BASE_URL = "https://etnews.com/news/section.html?id1=06"
KST = pytz.timezone('Asia/Seoul')

LIST_PAGE_HTML = """
<html><body>
    <ul class="news_list">
        <li>
            <a href="/20250628000001"></a>
            <div class="text"><strong>Article In Range</strong><span class="date">2025-06-28 10:00</span></div>
        </li>
        <li>
            <a href="/20230101000001"></a>
            <div class="text"><strong>Article Out of Range</strong><span class="date">2023-01-01 12:00</span></div>
        </li>
        <li>
            <a href="/20250628000002"></a>
            <div class="text"><strong>[포토] Photo Article</strong><span class="date">2025-06-28 11:00</span></div>
        </li>
        <li>
            <a href="/20250628000003"></a>
            <div class="text"><strong>Broken Date Article</strong><span class="date">날짜아님</span></div>
        </li>
    </ul>
</body></html>
"""

# --- Fixtures ---

@pytest.fixture
def crawler(mocker):
    """Fixture to create a NewsCrawlerEtnews instance with a mocked UserAgent."""
    mock_ua = mocker.patch('src.services.news_crawler_etnews.UserAgent')
    mock_ua.return_value.random = 'test-user-agent'
    instance = NewsCrawlerEtnews(base_url=BASE_URL)
    instance.set_target_date_range(KST.localize(dt.datetime(2025, 6, 27)),
                                   KST.localize(dt.datetime(2025, 6, 29)))
    return instance

# --- Test Cases ---

def test_parse_date(crawler):
    """Test parsing of ETNews's date format."""
    assert crawler._parse_date("2025-05-23 19:22") == dt.datetime(2025, 5, 23, 19, 22)
    assert crawler._parse_date("invalid-date") == PARSE_FAILED_DATETIME

def test_fetch_articles_filters_by_date_and_title(crawler):
    """기간 내 기사만 수집하고, [포토] 기사와 날짜 파싱 불가 기사는 제외한다."""
    with patch.object(crawler, '_fetch_html', return_value=LIST_PAGE_HTML):
        articles = crawler.fetch_articles(target_page_num=1)

    assert [a['title'] for a in articles] == ["Article In Range"]
    assert articles[0]['url'] == "https://etnews.com/20250628000001"
    assert articles[0]['published_date'] == "2025-06-28"

def test_fetch_articles_skips_failed_page(crawler):
    """목록 페이지 요청이 실패(None)해도 예외 없이 나머지 페이지의 기사를 수집한다."""
    with patch.object(crawler, '_fetch_html', side_effect=[None, LIST_PAGE_HTML]):
        articles = crawler.fetch_articles(target_page_num=2)

    assert [a['title'] for a in articles] == ["Article In Range"]

def test_fetch_articles_keeps_previous_pages_when_list_missing(crawler):
    """뒤 페이지에 기사 목록이 없어도 앞 페이지에서 수집한 기사를 버리지 않는다."""
    empty_page_html = "<html><body><p>no list here</p></body></html>"
    with patch.object(crawler, '_fetch_html', side_effect=[LIST_PAGE_HTML, empty_page_html]):
        articles = crawler.fetch_articles(target_page_num=2)

    assert [a['title'] for a in articles] == ["Article In Range"]
