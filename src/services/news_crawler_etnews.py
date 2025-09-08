import os
import sys
import logging
import traceback
import re
import json
import time
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

class NewsCrawlerEtnews:
    """
    etnews.com의 '전자', 'SW', 'IT' 섹션에서 뉴스 기사 목록과 본문을 수집하는 크롤러 클래스.
    - 지정된 섹션의 최신 뉴스 목록을 가져옵니다.
    - 제목이 '[포토]'로 시작하는 기사는 수집에서 제외합니다.
    - 각 기사의 상세 페이지에 접속하여 본문 내용을 수집합니다.
    """
        
    target_section_en_dict = {
        "06": "electronics",
        "04": "software",
        "03": "it",
    }
    
    def __init__(self, base_url: str):
        self.base_url = base_url   
        # 랜덤 User-Agent 생성
        self.ua = UserAgent()
        self._update_headers()
        
        self.end_date = dt.datetime.now(kst_timezone)
        self.start_date = self.end_date - dt.timedelta(days=3)
        
        logger.info(f"ETNews NewsCrawler initialized for base URL: {self.base_url}")
        
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
        ETNews 날짜 형식: YYYY.MM.DD HH:MM  (예: 2025-05-23 19:22)
        """
        try:
            return dt.datetime.strptime(datetime_str, "%Y-%m-%d %H:%M")
        except ValueError as e:
            logger.error(f"Failed to parse date string '{datetime_str}': {e}")
            return dt.datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함

    def _fetch_html(self, url: str):
        """
        주어진 URL의 HTML 콘텐츠를 가져옵니다.
        :param url: HTML을 가져올 URL
        :return: 성공 시 HTML 문자열, 실패 시 None
        """
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()  # HTTP 오류 발생 시 예외 발생
            response.encoding = 'utf-8' # 인코딩 설정
            return response.text
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching URL {url}: {e}")
            return None
        
    def fetch_articles(self, target_page_num: int = 1) -> List[Dict[str, str]]:
        """
        뉴스 목록 페이지에서 기사 제목, URL, 날짜를 가져와 설정된 일자 (디폴트 최근 3일) 이내 뉴스만 필터링합니다.
        ETNews 의 HTML 구조에 맞춰져 있습니다.
        """
        news_list = []
        
        for page_num in range(1, target_page_num + 1):
            
            logger.info(f"Fetching articles from {self.base_url + f'&page={page_num}'} published from {self.start_date} to {self.end_date}")
        
            html = self._fetch_html(self.base_url + f'&page={page_num}')
            soup = BeautifulSoup(html, 'html.parser')
            
            # 데스크탑 페이지 구조: 기사 목록은 'div' 태그와 'class=list_news' 안에 'ul > li' 형태로 존재        
            article_list_ul = soup.find('ul', class_='news_list')
            if not article_list_ul:
                logger.warning("Could not find 'ul' inside the news_list container.")
                return []

            list_items = article_list_ul.find_all('li')

            for item in list_items:
                link_tag = item.find('a')
                div_text_tag = item.find('div', class_="text")
                if not link_tag or not div_text_tag:
                    continue

                title_tag = div_text_tag.find('strong')
                date_tag = div_text_tag.find('span', class_='date')
                if not title_tag or not date_tag:
                    continue            
                
                article_link = link_tag['href']
                # 상대 경로 URL을 절대 경로로 변환
                # base_url이 'https://etnews.com/news/section.html?id1=06' 이면
                # 'https://etnews.com' 부분만 추출하여 합칩니다.
                base_domain = self.base_url.split('/news')[0]
                article_url = base_domain + article_link
                
                title = title_tag.get_text(strip=True)
                article_datetime = date_tag.get_text(strip=True)
                
                # '[포토]'로 시작하는 제목은 건너뜀
                if title.startswith('[포토]'):
                    continue
                
                logger.info("=========================")
                logger.info(f'title: {title}')
                logger.info(f'link: {article_link}')
                logger.info(f'article_datetime: {article_datetime}')
                            
                published_datetime = self._parse_date(article_datetime)
                
                # Check if the article is within from start_date to end_date
                published_datetime = kst_timezone.localize(published_datetime)
                if published_datetime >= self.start_date and published_datetime <= self.end_date:            
                    news_list.append({
                        'title': title,
                        'url': article_url, 
                        "published_date": published_datetime.strftime('%Y-%m-%d'),                
                        "content": ""  # 내용은 fetch_article_content에서 채움               
                    })
                else:
                    logger.debug(f"Skipping old article: {title} ({published_datetime})")
            
        return news_list

    def _parse_article_content(self, html: str) -> str:
        """
        개별 기사 페이지의 HTML에서 본문 텍스트를 파싱합니다.
        :param html: 기사 페이지의 HTML 콘텐츠
        :return: 기사 본문 텍스트
        """
        soup = BeautifulSoup(html, 'html.parser')
        
        # 본문은 'div' 태그와 'article_body' 클래스에 포함되어 있음
        content_div = soup.find('div', class_='article_body')
        
        if content_div:
            # 기사 본문 내 불필요한 태그(광고, 관련기사 등) 제거
            for unwanted_tag in content_div.find_all(['figure', 'script', 'div', 'table']):
                unwanted_tag.decompose()
            
            # 텍스트 추출 및 공백 정리
            text = content_div.get_text(separator='\n', strip=True)
            return text
        
        return "Content not found."

    def run(self) -> List[Dict[str, str]]:
        """
        정의된 모든 섹션에 대해 크롤링을 실행하고 결과를 반환합니다.
        :return: 수집된 전체 기사 데이터 리스트
        """
        logger.info("Start crawling etnews.com...")
        all_articles_data = []

        
        section_url = self.base_url
        section_name = self.target_section_en_dict[section_url.split('=')[-1]]
        logger.info(f"Crawling section: {section_name} ({section_url})")
        
        # 1. 섹션 페이지 HTML 가져오기
        list_page_html = self._fetch_html(section_url)
        if not list_page_html:
            logger.warning(f"Failed to fetch list page for section {section_name}. Skipping.")
            return all_articles_data
        
        # 2. 기사 링크 목록 파싱하기
        articles_to_crawl = self._parse_article_links(list_page_html)
        logger.info(f"Found {len(articles_to_crawl)} articles in section {section_name}")
        
        # # 3. 각 기사 본문 수집하기
        # for article_info in articles_to_crawl:
        #     logger.info(f"  - Fetching content for: {article_info['title']}")
            
        #     article_html = self._fetch_html(article_info['url'])
        #     if article_html:
        #         content = self._parse_article_content(article_html)
        #         article_info['content'] = content
        #         all_articles_data.append(article_info)
        #     else:
        #         logger.warning(f"Failed to fetch content for {article_info['url']}")

        #     # 서버 부하를 줄이기 위해 잠시 대기
        #     time.sleep(0.5)
        
        # logger.info(f"Crawling finished. Total {len(all_articles_data)} articles collected.")
        return all_articles_data
    
def main(target_section: str, base_ymd: str):
    """
    etnews 뉴스 수집 메인 배치 함수
    """
    
    section_url_dict = {
        "전자": "https://etnews.com/news/section.html?id1=06",
        "SW": "https://etnews.com/news/section.html?id1=04",
        "IT": "https://etnews.com/news/section.html?id1=03",
    }
    
    target_section_en_dict = {
        "전자": "electronics",
        "SW": "software",
        "IT": "it",
    }
    
    target_section_en = target_section_en_dict[target_section]
    
    ETNEWS_URL = section_url_dict[target_section]
    
    end_date = dt.datetime.strptime(base_ymd, "%Y%m%d")
    end_date = kst_timezone.localize(end_date) + dt.timedelta(hours=24)
    start_date = end_date - dt.timedelta(days=3)
    
    crawler = NewsCrawlerEtnews(ETNEWS_URL)
    crawler.set_target_date_range(start_date, end_date)
    
    try:
        articles = crawler.fetch_articles(target_page_num=4)
        
        # 뉴스 데이터 json 파일로 저장 
        with open(f'{pjt_home_path}/data/etnews_{target_section_en}_articles.json', 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        logger.info(f"Articles saved to etnews_{target_section_en}_articles.json")
        
    except Exception as e:
        msg = traceback.format_exc()
        logger.error(msg)
        sys.exit(1)    
    

if __name__ == '__main__':
    import argparse
    
    main(target_section='전자', base_ymd='20250908')