import os
import sys
import site
import logging

import requests
from bs4 import BeautifulSoup
from typing import List, Dict
from datetime import datetime, timedelta

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)


class NewsCrawler:
    """
    지정된 뉴스 웹사이트에서 기사를 크롤링하는 클래스.
    현재 ZDNet Korea의 반도체 뉴스 섹션에 맞춰져 있습니다.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        logger.info(f"NewsCrawler initialized for base URL: {self.base_url}")

    def _parse_date(self, date_str: str) -> datetime:
        """
        날짜 문자열을 datetime 객체로 파싱합니다.
        ZDNet 날짜 형식: YYYY.MM.DD (예: 2024.05.24)
        """
        try:
            return datetime.strptime(date_str.strip(), '%Y.%m.%d')
        except ValueError as e:
            logger.error(f"Failed to parse date string '{date_str}': {e}")
            return datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함

    def fetch_articles(self) -> List[Dict[str, str]]:
        """
        뉴스 목록 페이지에서 기사 제목, URL, 날짜를 가져와 최근 7일간의 뉴스만 필터링합니다.
        ZDNet Korea의 HTML 구조에 맞춰져 있습니다.
        """
        news_list = []
        seven_days_ago = datetime.now() - timedelta(days=7)
        logger.info(f"Fetching articles from {self.base_url} published after {seven_days_ago.strftime('%Y.%m.%d')}")

        try:
            response = requests.get(self.base_url, headers=self.headers, timeout=10)
            response.raise_for_status()  # HTTP 오류가 발생하면 예외 발생
            soup = BeautifulSoup(response.text, 'html.parser')

            # ZDNet 뉴스 목록 컨테이너 (예시 CSS 선택자, 실제 웹사이트 검사 필요)
            news_posts = soup.find_all('div', class_='newsPost')

            if not news_posts:
                logger.warning(f"No news items found with selector '.news_list_area li' on {self.base_url}")
                return news_list

            for item in news_posts:
                link_tag = item.select_one('a').get('href')  # 기사 제목과 URL을 포함하는 <a> 태그
                date_tag = item.find("p", class_="byline")  # 기사 날짜를 포함하는 태그 (예시: <span class="date">)

                if link_tag and 'href' in link_tag.attrs and date_tag:
                    title = link_tag.text.strip()
                    article_url = link_tag['href']
                    published_date_str = date_tag.text.strip()

                    # 상대 경로 URL을 절대 경로로 변환
                    if not article_url.startswith('http'):
                        # base_url이 'https://zdnet.co.kr/news/?lstcode=0050' 이면
                        # 'https://zdnet.co.kr' 부분만 추출하여 합칩니다.
                        base_domain = self.base_url.split('/news')[0]
                        article_url = base_domain + article_url

                    published_date = self._parse_date(published_date_str)

                    # 최근 7일 이내의 뉴스만 포함
                    if published_date >= seven_days_ago:
                        news_list.append({
                            "title": title,
                            "url": article_url,
                            "published_date": published_date.strftime('%Y.%m.%d'),
                            "content": ""  # 내용은 fetch_article_content에서 채움
                        })
                    else:
                        logger.debug(f"Skipping old article: {title} ({published_date_str})")
                else:
                    logger.debug(f"Skipping malformed news item: {item.prettify()}")

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching article list from {self.base_url}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred during article list fetching: {e}")
        return news_list

    def fetch_article_content(self, article_url: str) -> str:
        """
        개별 기사의 전체 내용을 가져옵니다.
        ZDNet Korea의 HTML 구조에 맞춰져 있습니다.
        """
        logger.info(f"Fetching content for article: {article_url}")
        try:
            response = requests.get(article_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # 기사 내용이 담긴 div/p 태그를 찾습니다. 실제 선택자로 변경 필요
            # ZDNet은 보통 'article_view_content' 같은 클래스를 사용합니다.
            content_div = soup.select_one('#article_view_content')  # 실제 선택자로 변경 필요

            if content_div:
                # 불필요한 태그 제거 (예: 광고, 이미지 캡션, 기자 정보 등)
                for script_or_style in content_div(
                        ["script", "style", "figure", "figcaption", "span.writer", "div.ad_area"]):
                    script_or_style.extract()

                paragraphs = content_div.find_all(['p', 'div', 'h1', 'h2', 'h3', 'li'])  # 텍스트 추출할 태그 지정
                content = "\n".join(
                    [p.get_text(separator=' ', strip=True) for p in paragraphs if p.get_text(strip=True)])
                logger.info(f"Successfully fetched content for {article_url}")
                return content.strip()
            else:
                logger.warning(
                    f"Could not find article content with selector '#article_view_content' for {article_url}")
                return "기사 내용을 찾을 수 없습니다."
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching article content from {article_url}: {e}")
            return f"기사 내용을 가져오는 데 실패했습니다: {e}"
        except Exception as e:
            logger.error(f"An unexpected error occurred while parsing {article_url}: {e}")
            return f"기사 내용을 파싱하는 데 실패했습니다: {e}"


# 로컬 테스트를 위한 예시 코드 (실제 네트워크 요청 발생)
if __name__ == "__main__":
    ZDNET_URL = "https://zdnet.co.kr/news/?lstcode=0050"  # ZDNet 반도체 뉴스 섹션 URL (가정)
    crawler = NewsCrawler(ZDNET_URL)

    logger.info(f"--- Fetching recent articles from {ZDNET_URL} ---")
    articles = crawler.fetch_articles()

    if articles:
        logger.info(f"Found {len(articles)} recent articles.")
        for i, article in enumerate(articles):
            logger.info(f"\n--- Article {i + 1} ---")
            logger.info(f"Title: {article['title']}")
            logger.info(f"URL: {article['url']}")
            logger.info(f"Published Date: {article['published_date']}")

            # 모든 기사 내용을 가져오면 시간이 오래 걸릴 수 있으므로, 몇 개만 가져오기
            if i < 2:  # 예시로 첫 2개 기사만 내용 가져오기
                content = crawler.fetch_article_content(article['url'])
                logger.info(f"Content Snippet (first 200 chars): {content[:200]}...")
            else:
                logger.info("Skipping content fetch for remaining articles for brevity.")
    else:
        logger.warning("No recent articles found or an error occurred.")