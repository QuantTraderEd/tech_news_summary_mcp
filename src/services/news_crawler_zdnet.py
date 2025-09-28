import os
import sys
import logging
import traceback
import re
import json
import datetime as dt

from typing import List, Dict

import pytz
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')

class NewsCrawler_ZDNet:
    """
    지정된 뉴스 웹사이트에서 기사를 크롤링하는 클래스.
    현재 ZDNet Korea의 반도체 뉴스 섹션에 맞춰져 있습니다.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        # 랜덤 User-Agent 생성
        self.ua = UserAgent()
        self._update_headers()
        
        self.end_date = dt.datetime.now(kst_timezone)
        self.start_date = self.end_date - dt.timedelta(days=3)
        
        logger.info(f"ZDNet NewsCrawler initialized for base URL: {self.base_url}")
        
    def set_target_date_range(self, start_date: dt.datetime, end_date: dt.datetime):
        """
        뉴스 기사 필터링 일자 설정 함수
        """
        self.start_date = start_date
        self.end_date = end_date
        logger.info(f"start_date=> {start_date}")
        logger.info(f"end_date=> {end_date}")
        
    def _update_headers(self):
        """랜덤한 User-Agent를 사용하여 헤더를 업데이트합니다."""
        self.headers = {
            'User-Agent': self.ua.random,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ko-KR,ko;q=0.8,en-US;q=0.5,en;q=0.3',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache'
        }

    def _parse_date(self, datetime_str: str) -> dt.datetime:
        """
        날짜 문자열을 datetime 객체로 파싱합니다.
        ZDNet 날짜 형식: YYYY.MM.DD AM HH:MM  (예: 2025.05.23 AM 11:22)
        """
        try:
            return dt.datetime.strptime(datetime_str, "%Y.%m.%d %p %I:%M")
        except ValueError as e:
            logger.error(f"Failed to parse date string '{datetime_str}': {e}")
            return dt.datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함
        
    def _parse_date_from_link(self, article_link: str):
        # 정규 표현식을 사용하여 숫자 부분 추출
        match = re.search(r'no=(\d+)', article_link)

        if match:
            # 추출된 숫자 문자열
            datetime_str = match.group(1)
            # datetime 객체로 파싱
            # %Y: 년도 (4자리), %m: 월 (2자리), %d: 일 (2자리)
            # %H: 시 (24시간 형식), %M: 분 (2자리), %S: 초 (2자리)
            datetime_object = dt.datetime.strptime(datetime_str, '%Y%m%d%H%M%S')
            return datetime_object
        else:
            logger.error(f"Failed to parse article_link: '{article_link}' ....")
            return dt.datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함

    def fetch_articles(self) -> List[Dict[str, str]]:
        """
        뉴스 목록 페이지에서 기사 제목, URL, 날짜를 가져와 설정된 일자 (디폴트 최근 3일) 이내 뉴스만 필터링합니다.
        ZDNet Korea의 HTML 구조에 맞춰져 있습니다.
        """
        news_list = []
        
        str_start_date = self.start_date.strftime('%Y-%m-%d')
        str_end_date = self.end_date.strftime('%Y-%m-%d')
        
        logger.info(f"Fetching articles from {self.base_url}published from {str_start_date} to {str_end_date}")

        response = requests.get(self.base_url, headers=self.headers, timeout=10)
        response.raise_for_status()  # HTTP 오류가 발생하면 예외 발생
        soup = BeautifulSoup(response.text, 'html.parser')

        # ZDNet 뉴스 목록 컨테이너 (예시 CSS 선택자, 실제 웹사이트 검사 필요)
        top_news = soup.find_all('div', class_='top_news')
        sub_news = soup.find_all('div', class_='sub_news')
        news_posts = soup.find_all('div', class_='newsPost')

        if (not news_posts) and (not sub_news):
            logger.warning(f"Not Found both news_posts and sub_news")
            logger.warning(f"No news items found on {self.base_url}")
            return news_list

        # news_post 클래스에서 검색된 뉴스 기사 목록
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

            # Check if the article is within from start_date to end_date
            published_datetime = kst_timezone.localize(published_datetime)
            if published_datetime >= self.start_date and published_datetime <= self.end_date:
                news_list.append({
                    "title": title,
                    "url": article_url,
                    "published_date": published_datetime.strftime('%Y-%m-%d'),
                    "content": ""  # 내용은 fetch_article_content에서 채움
                })
            else:
                logger.debug(f"Skipping old article: {title} ({published_datetime})")

        # sub_news 클래스에서 검색된 뉴스 기사 목록
        # To-Do: sub_news 뉴스 목록 데이터 있는 경우 데이터 처리 로직 추가 필요
        for news_item in sub_news:
            article_link = news_item.select_one('a').get('href')  # 기사 제목과 URL을 포함하는 <a> 태그            
            title = news_item.get_text(strip=True)
            article_datetime = self._parse_date_from_link(article_link)

            logger.info("=========================")
            logger.info(f'title: {title}')
            logger.info(f'link: {article_link}')
            logger.info(f'article_datetime: {article_datetime}')
            
            # 상대 경로 URL을 절대 경로로 변환
            # base_url이 'https://zdnet.co.kr/news/?lstcode=0050' 이면
            # 'https://zdnet.co.kr' 부분만 추출하여 합칩니다.
            base_domain = self.base_url.split('/news')[0]
            article_url = base_domain + article_link
            
            # Check if the article is within from start_date to end_date
            published_datetime = kst_timezone.localize(article_datetime)
            if published_datetime >= self.start_date and published_datetime <= self.end_date:
                news_list.append({
                    "title": title,
                    "url": article_url,
                    "published_date": published_datetime.strftime('%Y-%m-%d'),
                    "content": ""  # 내용은 fetch_article_content에서 채움
                })
            else:
                logger.debug(f"Skipping old article: {title} ({published_datetime})")
        
        # top_news 클래스에서 검색된 뉴스 기사 목록
        # To-Do: top_news 뉴스 목록 데이터 있는 경우 데이터 처리 로직 추가 필요
        for news_item in top_news:
            article_link = news_item.select_one('a').get('href')  # 기사 제목과 URL을 포함하는 <a> 태그            
            title = news_item.get_text(strip=True)
            article_datetime = self._parse_date_from_link(article_link)

            logger.info("=========================")
            logger.info(f'title: {title}')
            logger.info(f'link: {article_link}')
            logger.info(f'article_datetime: {article_datetime}')
            
            # 상대 경로 URL을 절대 경로로 변환
            # base_url이 'https://zdnet.co.kr/news/?lstcode=0050' 이면
            # 'https://zdnet.co.kr' 부분만 추출하여 합칩니다.
            base_domain = self.base_url.split('/news')[0]
            article_url = base_domain + article_link
            
            # Check if the article is within from start_date to end_date
            published_datetime = kst_timezone.localize(article_datetime)
            if published_datetime >= self.start_date and published_datetime <= self.end_date:
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
        try:
            response = requests.get(article_url, headers=self.headers, timeout=10)
            response.raise_for_status()
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout occurred while fetching content for {article_url}")
            return "타임아웃으로 기사 내용을 찾을 수 없습니다."
        
        soup = BeautifulSoup(response.text, 'html.parser')

        # 기사 내용이 담긴 div/p 태그를 찾습니다. 실제 선택자로 변경 필요
        # ZDNet은 보통 'article_view_content' 같은 클래스를 사용합니다.
        content_div = soup.select_one('#articleBody')  # 실제 선택자로 변경 필요
        if content_div is None: content_div = soup.find('div', class_='sub_view_cont')  # content 없는 경우 추가 탐색

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
        
def main(target_section: str, base_ymd: str):
    """
    zdnet 뉴스 수집 메인 배치 함수
    :param str target_section: 뉴스 수집 대상 색션 (반도체, 자동차, 배터리, 인공지능, 컴퓨팅)
    :param str base_ymd: 뉴스 수집 기준 일자 (yyyymmdd), 뉴스 수집 기본 일자 범위는 [T-3, T]
    """
    
    section_url_dict = {
        "반도체": "https://zdnet.co.kr/news/?lstcode=0050",
        "자동차": "https://zdnet.co.kr/news/?lstcode=0057&page=1",
        "배터리": "https://zdnet.co.kr/newskey/?lstcode=%EB%B0%B0%ED%84%B0%EB%A6%AC",
        "인공지능": "https://zdnet.co.kr/newskey/?lstcode=%EC%9D%B8%EA%B3%B5%EC%A7%80%EB%8A%A5",
        "컴퓨팅": "https://zdnet.co.kr/news/?lstcode=0020&page=1",
    }
    
    target_section_en_dict = {
        "반도체": "semiconductor",
        "자동차": "automotive",
        "배터리": "battery",
        "인공지능": "ai",
        "컴퓨팅": "computing",
    }
    
    target_section_en = target_section_en_dict[target_section]
    
    ZDNET_URL = section_url_dict[target_section]
    
    end_date = dt.datetime.strptime(base_ymd, "%Y%m%d")
    end_date = kst_timezone.localize(end_date) + dt.timedelta(hours=24)
    start_date = end_date - dt.timedelta(days=3)
    
    crawler = NewsCrawler_ZDNet(ZDNET_URL)
    crawler.set_target_date_range(start_date, end_date)
    
    try:
        logger.info(f"--- Fetching recent articles from {ZDNET_URL} ---")
        articles = crawler.fetch_articles()

        if articles:
            logger.info(f"Found {len(articles)} recent articles.")
            for i, article in enumerate(articles):
                logger.info(f"\n--- Article {i + 1} ---")
                logger.info(f"Title: {article['title']}")
                logger.info(f"URL: {article['url']}")
                logger.info(f"Published Date: {article['published_date']}")
        
                content = crawler.fetch_article_content(article['url'])
                logger.info(f"Content Snippet (first 200 chars): {content[:200]}...")
                article['content'] = content
                
        else:
            logger.warning("No recent articles found or an error occurred.")
            
        # 뉴스 데이터 json 파일로 저장 
        with open(f'{pjt_home_path}/data/zdnet_{target_section_en}_articles.json', 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        logger.info(f"Articles saved to zdnet_{target_section_en}_articles.json")
        
        
    except Exception as e:
        msg = traceback.format_exc()
        logger.error(msg)
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="zdnet 뉴스 수집 메인 배치 함수"
    )

    # target_section 인자 추가
    parser.add_argument(
        "target_section",
        type=str,        
        default="반도체",
        choices=["반도체", "자동차", "배터리", "인공지능", "컴퓨팅"],
        help="뉴스 수집 대상 색션 [%(choices)s] default=[%(default)s]",
        metavar='target_section',
        nargs='?'
    )

    # base_ymd 인자 추가
    parser.add_argument(
        "base_ymd",
        type=str,
        default=dt.datetime.now(kst_timezone).strftime("%Y%m%d"), # 기본값은 현재 날짜
        help="뉴스 수집 기준 일자 (yyyymmdd), 미입력 시 현재 날짜가 기본값",
        nargs='?'
    )

    args = parser.parse_args()

    # base_ymd 유효성 검증
    try:
        dt.datetime.strptime(args.base_ymd, "%Y%m%d")
    except ValueError:
        parser.error(f"잘못된 날짜 형식입니다: {args.base_ymd}. yyyymmdd 형식으로 입력해주세요.")

    main(target_section=args.target_section, base_ymd=args.base_ymd)
