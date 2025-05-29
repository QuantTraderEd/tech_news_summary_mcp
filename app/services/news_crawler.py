import logging
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict

import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

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
        # 랜덤 User-Agent 생성
        ua = UserAgent()
        self.headers = {
            'User-Agent': ua.random
        }        
        logger.info(f"NewsCrawler initialized for base URL: {self.base_url}")

    def _parse_date(self, datetime_str: str) -> datetime:
        """
        날짜 문자열을 datetime 객체로 파싱합니다.
        ZDNet 날짜 형식: YYYY.MM.DD AM HH:MM  (예: 2025.05.23 AM 11:22)
        """
        try:
            return datetime.strptime(datetime_str, "%Y.%m.%d %p %I:%M")
        except ValueError as e:
            logger.error(f"Failed to parse date string '{datetime_str}': {e}")
            return datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함

    def fetch_articles(self) -> List[Dict[str, str]]:
        """
        뉴스 목록 페이지에서 기사 제목, URL, 날짜를 가져와 최근 7일간의 뉴스만 필터링합니다.
        ZDNet Korea의 HTML 구조에 맞춰져 있습니다.
        """
        news_list = []
        seven_days_ago = datetime.now() - timedelta(days=7)
        logger.info(f"Fetching articles from {self.base_url} published after {seven_days_ago.strftime('%Y.%m.%d')}")


        response = requests.get(self.base_url, headers=self.headers, timeout=10)
        response.raise_for_status()  # HTTP 오류가 발생하면 예외 발생
        soup = BeautifulSoup(response.text, 'html.parser')

        # ZDNet 뉴스 목록 컨테이너 (예시 CSS 선택자, 실제 웹사이트 검사 필요)
        news_posts = soup.find_all('div', class_='newsPost')

        if not news_posts:
            logger.warning(f"No news items found with selector '.news_list_area li' on {self.base_url}")
            return news_list

        for post in news_posts:

            article_link = post.select_one('a').get('href')  # 기사 제목과 URL을 포함하는 <a> 태그
            title_tag = post.find("div", class_="assetText").find('h3')
            title = title_tag.get_text(strip=True) if title_tag else None
            date_tag = post.find("p", class_="byline")
            span_tag = date_tag.find("span") if date_tag else None
            article_datetime = span_tag.get_text(strip=True) if span_tag else None

            logger.info("=========================")
            logger.info(f'title: {title}')
            logger.info(f'link: {article_link}')
            logger.info(f'article_datetime: {article_datetime}')

            if title is None or article_datetime is None:
                logger.debug(f"news item: {post.prettify()}")
                logger.warning("skip article post...")
                continue

            # 상대 경로 URL을 절대 경로로 변환
            # base_url이 'https://zdnet.co.kr/news/?lstcode=0050' 이면
            # 'https://zdnet.co.kr' 부분만 추출하여 합칩니다.
            base_domain = self.base_url.split('/news')[0]
            article_url = base_domain + article_link
            published_datetime = self._parse_date(article_datetime)

            # 최근 7일 이내의 뉴스만 포함
            if published_datetime >= seven_days_ago:
                news_list.append({
                    "title": title,
                    "url": article_url,
                    "published_date": published_datetime.strftime('%Y-%m-%d'),
                    "content": ""  # 내용은 fetch_article_content에서 채움
                })
            else:
                logger.debug(f"Skipping old article: {title} ({published_datetime})")

        return news_list

    def fetch_article_content(self, article_url: str) -> str:
        """
        개별 기사의 전체 내용을 가져옵니다.
        ZDNet Korea의 HTML 구조에 맞춰져 있습니다.
        """
        logger.info(f"Fetching content for article: {article_url}")
        
        response = requests.get(article_url, headers=self.headers, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')

        # 기사 내용이 담긴 div/p 태그를 찾습니다. 실제 선택자로 변경 필요
        # ZDNet은 보통 'article_view_content' 같은 클래스를 사용합니다.
        content_div = soup.select_one('#articleBody')  # 실제 선택자로 변경 필요

        if content_div:
            # 불필요한 태그 제거 (예: 광고, 이미지 캡션, 기자 정보 등)
            for script_or_style in content_div(
                    ["script", "style", "figure", "figcaption", "span.writer", "div.ad_area"]):
                script_or_style.extract()
                
            # 관련 기사 내용 제거
            # 1. h2 태그 중 "관련기사" 텍스트를 포함한 요소와 그 이후 내용 제거
            h2_tags = content_div.find_all('h2')
            for h2 in h2_tags:
                h2_text = h2.get_text(strip=True)
                if '관련기사' in h2_text or '관련 기사' in h2_text:
                    # h2 태그와 그 이후의 모든 형제 요소들을 제거
                    current = h2
                    while current:
                        next_sibling = current.next_sibling
                        current.extract()
                        current = next_sibling
                    break

            # 2. div class="news_box connect" 요소 제거
            related_news_divs = content_div.find_all('div', class_=['news_box', 'connect'])
            for div in related_news_divs:
                div.extract()

            # 3. div class="news_box connect" (클래스가 함께 있는 경우) 제거
            related_news_combined = content_div.find_all('div', class_='news_box connect')
            for div in related_news_combined:
                div.extract()

            # 4. 추가적인 관련 기사 섹션 제거 (다양한 패턴 대응)
            # "관련기사", "추천기사", "인기기사" 등의 텍스트를 포함한 div 제거
            related_keywords = ['관련기사', '관련 기사', '추천기사', '추천 기사', '인기기사', '인기 기사', '더보기']
            all_divs = content_div.find_all('div')
            for div in all_divs:
                div_text = div.get_text(strip=True)
                if any(keyword in div_text for keyword in related_keywords):
                    # 해당 div가 관련 기사 섹션인지 확인 (짧은 텍스트이거나 링크가 많은 경우)
                    if len(div_text) < 100 or len(div.find_all('a')) > 2:
                        div.extract()

            paragraphs = content_div.find_all(['p', 'div', 'h1', 'h2', 'h3', 'li'])  # 텍스트 추출할 태그 지정
            content = "\n".join(
                [p.get_text(separator=' ', strip=True) for p in paragraphs if p.get_text(strip=True)])
            logger.info(f"Successfully fetched content for {article_url}")
            return content.strip()
        else:
            logger.warning(
                f"Could not find article content with selector '#article_view_content' for {article_url}")
            return "기사 내용을 찾을 수 없습니다."
        


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
            if i < 10:  # 예시로 첫 2개 기사만 내용 가져오기
                content = crawler.fetch_article_content(article['url'])
                logger.info(f"Content Snippet (first 200 chars): {content[:200]}...")
            else:
                logger.info("Skipping content fetch for remaining articles for brevity.")
    else:
        logger.warning("No recent articles found or an error occurred.")