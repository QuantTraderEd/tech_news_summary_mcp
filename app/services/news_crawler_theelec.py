import logging
import os
import sys
import datetime as dt
from typing import List, Dict
import re

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


class ThelecNewsCrawler:
    """
    디일렉(thelec.kr) 뉴스 웹사이트에서 기사를 크롤링하는 클래스.
    IT·게임 섹션의 뉴스를 가져옵니다.
    """

    def __init__(self, base_url: str, target_section: str = "반도체"):
        self.base_url = base_url
        self.target_section = target_section  # 필터링할 섹션 추가
        self.ua = UserAgent()
        self._update_headers()
        logger.info(f"ThelecNewsCrawler initialized for base URL: {self.base_url}")
        logger.info(f"Target section: {self.target_section}")

    def _is_target_section(self, article_element) -> bool:
        """
        기사가 목표 섹션에 속하는지 확인합니다.
        섹션 정보는 기사 요소와 주변 요소의 텍스트에서 찾습니다.
        """
        if not self.target_section:
            return True  # 섹션 필터링 없음
            
        # 기사 요소와 주변 요소에서 섹션 정보 찾기
        # article_element가 BeautifulSoup 태그 객체라고 가정
        text_to_check = []
        if article_element:
            text_to_check.append(article_element.get_text())
            # 부모 요소의 텍스트도 확인하여 섹션 정보가 상위에 있을 경우를 대비
            if article_element.parent:
                text_to_check.append(article_element.parent.get_text())
            # 형제 요소의 텍스트도 확인 (섹션 태그가 기사 옆에 있을 수 있음)
            if article_element.find_previous_sibling():
                text_to_check.append(article_element.find_previous_sibling().get_text())
            if article_element.find_next_sibling():
                text_to_check.append(article_element.find_next_sibling().get_text())
        
        # 섹션 표시를 찾기 위한 키워드들
        section_keywords = {
            "반도체": ["반도체", "semiconductor", "S1N2"],
            "디스플레이": ["디스플레이", "display"],
            "IT‧게임": ["IT‧게임", "IT·게임", "IT게임"],
            "방산‧에너지": ["방산‧에너지", "방산·에너지", "방산에너지"]
        }
        
        target_keywords = section_keywords.get(self.target_section, [self.target_section])
        
        for indicator in text_to_check:
            for keyword in target_keywords:
                if keyword.lower() in indicator.lower(): # 대소문자 구분 없이 검색
                    return True
                            
        return False

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
        디일렉 날짜 형식: YYYY-MM-DD HH:MM (예: 2025-05-29 08:52)
        """
        try:
            # 시간 정보가 포함된 경우
            if ':' in datetime_str:
                return dt.datetime.strptime(datetime_str.strip(), "%Y-%m-%d %H:%M")
            # 날짜만 있는 경우
            else:
                return dt.datetime.strptime(datetime_str.strip(), "%Y-%m-%d")
        except ValueError as e:
            logger.error(f"Failed to parse date string '{datetime_str}': {e}")
            return dt.datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함

    def _extract_date_from_element(self, element) -> str | None:
        """
        주어진 BeautifulSoup 요소에서 날짜 정보를 추출합니다.
        다양한 위치에서 날짜를 찾을 수 있도록 개선합니다.
        """
        # 요소 자체의 텍스트에서 날짜 패턴 검색
        text = element.get_text(strip=True)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', text)
        if date_match:
            return f"{date_match.group(1)} {date_match.group(2)}"
        
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if date_match:
            return date_match.group(1)

        # 자식 요소들 중 날짜 정보를 포함할 가능성이 있는 요소 탐색
        # 예: <span class="date">, <div class="info"> 등
        possible_date_tags = element.find_all(['span', 'div', 'p', 'em', 'td'])
        for tag in possible_date_tags:
            tag_text = tag.get_text(strip=True)
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', tag_text)
            if date_match:
                return f"{date_match.group(1)} {date_match.group(2)}"
            date_match = re.search(r'(\d{4}-\d{2}-\d{2})', tag_text)
            if date_match:
                return date_match.group(1)
        
        return None

    def _get_published_date_from_article_page(self, article_url: str) -> dt.datetime | None:
        """
        개별 기사 URL을 방문하여 해당 페이지에서 발행 날짜와 시간을 추출합니다.
        예시: '승인 2025.05.20 08:20' 또는 '2025-05-20 08:20' 형식에서 날짜와 시간 추출.
        반환 값은 datetime 객체입니다.
        """
        logger.info(f"Fetching article page to extract published date: {article_url}")
        try:
            self._update_headers()
            response = requests.get(article_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            # 날짜와 시간 정보를 포함할 가능성이 있는 요소들을 탐색
            # 디일렉 사이트의 기사 페이지에서 날짜 정보가 주로 'div.info' 또는 'ul.info' 내에 있음
            # 또는 'article-view-info' 같은 클래스 내
            possible_date_containers = soup.find_all(
                ['div', 'ul', 'span', 'p'],
                class_=re.compile(r'info|date|viewinfo|article-view-info', re.I)
            )
            
            date_str = None
            for container in possible_date_containers:
                text = container.get_text(strip=True)
                # '승인 YYYY.MM.DD HH:MM' 또는 'YYYY.MM.DD HH:MM' 형식에 맞는 패턴 검색
                date_match = re.search(r'(?:승인\s*)?(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2})', text)
                if date_match:
                    # 'YYYY.MM.DD HH:MM' -> 'YYYY-MM-DD HH:MM' 형식으로 변환
                    date_str = f"{date_match.group(1).replace('.', '-')}"
                    time_str = date_match.group(2)
                    return self._parse_date(f"{date_str} {time_str}")
                
                # 다른 일반적인 날짜 형식도 고려 (예: 'YYYY-MM-DD HH:MM')
                date_match_alt = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', text)
                if date_match_alt:
                    return self._parse_date(f"{date_match_alt.group(1)} {date_match_alt.group(2)}")
                
                # 날짜만 있는 경우 (YYYY.MM.DD 또는 YYYY-MM-DD)
                date_only_match = re.search(r'(?:승인\s*)?(\d{4}\.\d{2}\.\d{2})', text)
                if date_only_match:
                    date_str = f"{date_only_match.group(1).replace('.', '-')}"
                    return self._parse_date(date_str)

                date_only_match_alt = re.search(r'(\d{4}-\d{2}-\d{2})', text)
                if date_only_match_alt:
                    return self._parse_date(date_only_match_alt.group(1))

            logger.warning(f"Could not find date/time on article page: {article_url}")
            return None

        except requests.RequestException as e:
            logger.error(f"Network error fetching article page {article_url} for date extraction: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing article page {article_url} for date extraction: {e}")
            return None

    def _extract_section_from_page(self, soup) -> List[Dict[str, str]]:
        """
        페이지에서 기사 목록을 추출하고 목표 섹션 및 최근 7일 이내 기사만 필터링합니다.
        디일렉 웹사이트의 HTML 구조를 기반으로 기사를 찾습니다.
        """
        articles = []
        seven_days_ago = dt.datetime.now() - dt.timedelta(days=7)
        
        # 기사 목록을 포함할 가능성이 있는 주요 컨테이너 찾기
        potential_article_elements = []
        
        # Case 1: articles within <tr> tags (테이블 내 기사)
        article_rows = soup.find_all('tr')
        if article_rows:
            potential_article_elements.extend(article_rows)
            logger.debug(f"Found {len(article_rows)} potential article rows.")
        
        # Case 2: articles within <div> or <li> elements with specific classes (리스트 형태 기사)
        list_items = soup.find_all(['div', 'li'], class_=re.compile(r'article|list-item|news-item', re.I))
        if list_items:
            potential_article_elements.extend(list_items)
            logger.debug(f"Found {len(list_items)} potential article list items.")

        # Case 3: Direct links to articles if no clear container is found (직접 링크)
        direct_article_links = soup.find_all('a', href=re.compile(r'/news/articleView\.html\?idxno=\d+'))
        if direct_article_links:
            potential_article_elements.extend(direct_article_links)
            logger.debug(f"Found {len(direct_article_links)} direct article links.")

        # 중복 제거 (BeautifulSoup 객체는 hashable하지 않으므로, str(element)로 비교)
        seen_elements_text = set()
        unique_potential_articles = []
        for element in potential_article_elements:
            element_str = str(element) # 문자열로 변환하여 해싱
            if element_str not in seen_elements_text:
                seen_elements_text.add(element_str)
                unique_potential_articles.append(element)
        
        logger.info(f"Total unique potential article elements to process: {len(unique_potential_articles)}")

        for element in unique_potential_articles:
            # 기사 링크 찾기
            article_link = element.find('a', href=re.compile(r'/news/articleView\.html\?idxno=\d+'))
            
            if not article_link:
                # 요소 자체가 기사 링크인 경우 (Case 3)
                if element.name == 'a' and re.match(r'/news/articleView\.html\?idxno=\d+', element.get('href', '')):
                    article_link = element
                else:
                    continue # 기사 링크가 없으면 건너김

            # 섹션 필터링 적용
            if not self._is_target_section(element): # element 전체를 넘겨서 주변 텍스트도 검사
                logger.debug(f"Skipping article (section mismatch): {article_link.get_text(strip=True) if article_link else 'N/A'}")
                continue

            title = article_link.get_text(strip=True)
            if not title or len(title) < 5: # 너무 짧은 제목은 제외
                logger.debug(f"Skipping article (short title): {title}")
                continue

            href = article_link.get('href')
            if href.startswith('/'):
                article_url = 'https://www.thelec.kr' + href
            else:
                article_url = href # 이미 전체 URL인 경우

            published_datetime = None # Initialize published_datetime

            # 1. Try to extract date from the list page element
            date_text_from_list = self._extract_date_from_element(element)
            
            if date_text_from_list:
                published_datetime = self._parse_date(date_text_from_list)
            else:
                # 2. If not found in list page, try to fetch from the article page
                logger.debug(f"Date not found in list element for '{title}', trying article page: {article_url}")
                published_datetime = self._get_published_date_from_article_page(article_url)

            if not published_datetime: # If date still not found after both attempts
                logger.debug(f"Skipping article (no date found after all attempts): {title}")
                continue

            logger.debug(f"Processing article: Title='{title}', URL='{article_url}', Published_datetime='{published_datetime}'")

            # 최근 7일 이내의 뉴스만 포함
            if published_datetime >= seven_days_ago:
                articles.append({
                    "title": title,
                    "url": article_url,
                    "published_date": published_datetime.strftime('%Y-%m-%d'),
                    "author": "", # 저자 정보는 여기서 추출하기 어려울 수 있음, 필요하면 fetch_article_content에서 보강
                    "content": ""  # 내용은 fetch_article_content에서 채움
                })
                logger.info(f"Found target section article: {title}")
            else:
                logger.debug(f"Skipping old article: {title} ({published_datetime})")
        
        return articles

    def fetch_articles(self, pages: int = 3) -> List[Dict[str, str]]:
        """
        뉴스 목록 페이지에서 기사 제목, URL, 날짜를 가져와 최근 7일간의 뉴스만 필터링합니다.
        디일렉의 HTML 구조에 맞춰져 있으며, 특정 섹션만 필터링합니다.
        """
        news_list = []
        seven_days_ago = dt.datetime.now() - dt.timedelta(days=7)
        logger.info(f"Fetching articles from {self.base_url} published after {seven_days_ago.strftime('%Y-%m-%d')}")
        logger.info(f"Filtering for section: {self.target_section}")

        for page in range(1, pages + 1):
            try:
                # 페이지 파라미터를 추가하여 여러 페이지 크롤링
                if 'page=' in self.base_url:
                    url = re.sub(r'page=\d+', f'page={page}', self.base_url)
                else:
                    # base_url에 이미 쿼리 파라미터가 있을 수 있으므로 &를 사용
                    url = self.base_url + (f"&page={page}" if "?" in self.base_url else f"?page={page}")
                
                logger.info(f"Fetching page {page}: {url}")
                
                # 매 요청마다 새로운 User-Agent 사용
                self._update_headers()
                
                response = requests.get(url, headers=self.headers, timeout=15)
                response.raise_for_status()  # HTTP 오류가 발생하면 예외 발생
                response.encoding = 'utf-8'  # 한글 인코딩 설정
                soup = BeautifulSoup(response.text, 'html.parser')

                # 새로운 섹션별 파싱 방법 사용
                section_articles = self._extract_section_from_page(soup)
                news_list.extend(section_articles)
                
            except requests.RequestException as e:
                logger.error(f"Error fetching page {page}: {e}")
                continue
            except Exception as e:
                logger.error(f"Unexpected error on page {page}: {e}")
                continue

        # 중복 제거 (URL 기준)
        seen_urls = set()
        unique_articles = []
        for article in news_list:
            if article['url'] not in seen_urls:
                seen_urls.add(article['url'])
                unique_articles.append(article)
        
        logger.info(f"Found {len(unique_articles)} unique articles in target section")
        return unique_articles

    def fetch_article_content(self, article_url: str) -> str:
        """
        개별 기사의 전체 내용을 가져옵니다.
        디일렉의 HTML 구조에 맞춰져 있습니다.
        """
        logger.info(f"Fetching content for article: {article_url}")
        
        try:
            # 매 요청마다 새로운 User-Agent 사용
            self._update_headers()
            
            response = requests.get(article_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            # 디일렉 기사 내용 영역 찾기
            # 여러 가능한 선택자 시도
            content_selectors = [
                'div.article-content',
                'div.news-content', 
                'div.view-content',
                'div#article-content',
                'div.article_content',
                'div.articleContent',
                'div[id*="content"]',
                'div[class*="content"]',
                'div.user-content' # thelec.kr에서 기사 본문이 이 클래스를 사용하는 경우가 있음
            ]

            content_div = None
            for selector in content_selectors:
                content_div = soup.select_one(selector)
                if content_div:
                    break

            # 기본 접근법: article 태그나 main 태그 찾기
            if not content_div:
                content_div = soup.find('article') or soup.find('main')

            # 마지막 시도: 가장 큰 텍스트 블록 찾기
            if not content_div:
                potential_content = soup.find_all('div', string=re.compile(r'.{100,}'))
                if potential_content:
                    content_div = max(potential_content, key=lambda x: len(x.get_text()))

            if content_div:
                # 불필요한 태그 제거
                for unwanted in content_div(["script", "style", "nav", "header", "footer", 
                                             "aside", "figure", "figcaption", "iframe", "img", "a"]): # 이미지와 링크도 제거
                    unwanted.extract()

                # 광고나 관련 기사 섹션 제거
                for ad_class in ['ad', 'advertisement', 'related', 'recommend', 'popular', 'copyright']: 
                    for element in content_div.find_all(attrs={'class': re.compile(ad_class, re.I)}):
                        element.extract()

                # 관련 기사 제목이 포함된 요소 제거
                for heading in content_div.find_all(['h2', 'h3', 'h4']):
                    heading_text = heading.get_text(strip=True)
                    if any(keyword in heading_text for keyword in ['관련기사', '관련 기사', '추천기사', '인기기사', '저작권']): 
                        # 해당 heading과 그 이후 모든 형제 요소 제거
                        current = heading
                        while current:
                            next_sibling = current.next_sibling
                            current.extract()
                            current = next_sibling
                        break

                # 텍스트 추출
                paragraphs = content_div.find_all(['p', 'div', 'span'])
                content_parts = []
                for p in paragraphs:
                    text = p.get_text(separator=' ', strip=True)
                    if text and len(text) > 10:  # 너무 짧은 텍스트는 제외
                        content_parts.append(text)

                content = '\n'.join(content_parts)
                
                # 중복된 줄바꿈 제거
                content = re.sub(r'\n\s*\n', '\n\n', content)
                
                logger.info(f"Successfully fetched content for {article_url}")
                return content.strip()
            else:
                logger.warning(f"Could not find article content for {article_url}")
                return "기사 내용을 찾을 수 없습니다."
                
        except requests.RequestException as e:
            logger.error(f"Network error fetching {article_url}: {e}")
            return f"네트워크 오류: {e}"
        except Exception as e:
            logger.error(f"Error parsing content from {article_url}: {e}")
            return f"파싱 오류: {e}"


# 로컬 테스트를 위한 예시 코드
if __name__ == "__main__":
    THELEC_URL = "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N2&view_type=sm"
    
    # 반도체 섹션만 크롤링하도록 설정
    crawler = ThelecNewsCrawler(THELEC_URL, target_section="반도체")

    logger.info(f"--- Fetching recent semiconductor articles from {THELEC_URL} ---")
    articles = crawler.fetch_articles(pages=2)  # 2페이지까지 크롤링

    if articles:
        logger.info(f"Found {len(articles)} recent semiconductor articles.")
        for i, article in enumerate(articles):
            logger.info(f"\n--- Article {i + 1} ---")
            logger.info(f"Title: {article['title']}")
            logger.info(f"URL: {article['url']}")
            logger.info(f"Published Date: {article['published_date']}")
        
            # 처음 3개 기사만 내용 가져오기 (시간 절약을 위해)
            if i < 3:
                content = crawler.fetch_article_content(article['url'])
                logger.info(f"Content Snippet (first 300 chars): {content[:300]}...")
                article['content'] = content
            else:
                logger.info("Skipping content fetch for remaining articles for brevity.")
    else:
        logger.warning("No recent semiconductor articles found or an error occurred.")

    # 결과를 JSON 파일로 저장 (선택사항)
    try:
        import json
        with open('thelec_semiconductor_articles.json', 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        logger.info("Articles saved to thelec_semiconductor_articles.json")
    except ImportError:
        logger.info("JSON module not available, skipping file save.")
