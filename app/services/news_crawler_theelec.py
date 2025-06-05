import os
import sys
import logging
import traceback
import datetime as dt
import re
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
        
        self.end_date = dt.datetime.now(kst_timezone)
        self.start_date = self.end_date - dt.timedelta(days=3)
        logger.info(f"ThelecNewsCrawler initialized for base URL: {self.base_url}")
        logger.info(f"Target section: {self.target_section}")
        
    def set_target_date_range(self, start_date: dt.datetime, end_date: dt.datetime):
        """
        뉴스 기사 필터링 일자 설정 함수
        """
        self.start_date = start_date
        self.end_date = end_date

    def _is_target_section(self, article_element) -> bool:
        """
        기사가 목표 섹션에 속하는지 확인합니다.
        섹션 정보는 기사 요소와 주변 요소의 텍스트에서 찾습니다.
        이 함수는 목록 페이지의 요소만을 기반으로 판단합니다.
        """
        if not self.target_section:
            return True  # 섹션 필터링 없음
            
        text_to_check = []
        if article_element:
            section_tag = article_element.find('small', class_="list-section")
            if not section_tag:
                logger.warning(f"The article element is missing section tag: {article_element.get_text()}") 
                return False
            text_to_check.append(section_tag.get_text())
        
        section_keywords = {
            "반도체": ["반도체", "semiconductor", "S1N2"],
            "디스플레이": ["디스플레이", "display"],
            "IT‧게임": ["IT‧게임", "IT·게임", "IT게임"],
            "방산‧에너지": ["방산‧에너지", "방산·에너지", "방산에너지"],
            "중국산업동향": ["중국산업동향", "China Industry Trends"],
            "배터리": ["배터리", "battery", "Battery"],
            "자동차": ["자동차", "car", "automotive"]
        }
        
        target_keywords = section_keywords.get(self.target_section, [self.target_section])
        
        for indicator in text_to_check:
            for keyword in target_keywords:
                if keyword.lower() in indicator.lower():
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
        디일렉 날짜 형식:YYYY-MM-DD HH:MM (예: 2025-05-29 08:52)
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
            return dt.datetime.min

    def _extract_date_from_element(self, element) -> str | None:
        """
        주어진 BeautifulSoup 요소에서 날짜 정보를 추출합니다.
        다양한 위치에서 날짜를 찾을 수 있도록 개선합니다.
        """
        text = element.get_text(strip=True)
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', text)
        if date_match:
            return f"{date_match.group(1)} {date_match.group(2)}"
        
        date_match = re.search(r'(\d{4}-\d{2}-\d{2})', text)
        if date_match:
            return date_match.group(1)

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
        """
        logger.info(f"Fetching article page to extract published date: {article_url}")
        try:
            self._update_headers()
            response = requests.get(article_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            possible_date_containers = soup.find_all(
                ['div', 'ul', 'span', 'p'],
                class_=re.compile(r'info|date|viewinfo|article-view-info', re.I)
            )
            
            for container in possible_date_containers:
                text = container.get_text(strip=True)
                date_match = re.search(r'(?:승인\s*)?(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2})', text)
                if date_match:
                    date_str = f"{date_match.group(1).replace('.', '-')}"
                    time_str = date_match.group(2)
                    return self._parse_date(f"{date_str} {time_str}")
                
                date_match_alt = re.search(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})', text)
                if date_match_alt:
                    return self._parse_date(f"{date_match_alt.group(1)} {date_match_alt.group(2)}")
                
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
            msg = traceback.format_exc()
            logger.error(msg)
            logger.error(f"Network error fetching article page {article_url} for date extraction: {e}")
            return None
        except Exception as e:
            msg = traceback.format_exc()
            logger.error(msg)
            logger.error(f"Error parsing article page {article_url} for date extraction: {e}")
            return None

    def _get_section_from_article_page(self, article_url: str) -> str | None:
        """
        개별 기사 URL을 방문하여 해당 페이지의 <meta property="article:section"/> 태그에서 섹션 정보를 추출합니다.
        """
        logger.debug(f"Fetching article page to extract section from meta tag: {article_url}")
        try:
            self._update_headers()
            response = requests.get(article_url, headers=self.headers, timeout=10) # Shorter timeout for just meta
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            meta_section_tag = soup.find('meta', property='article:section')
            if meta_section_tag:
                section_content = meta_section_tag.get('content', '').strip()
                logger.debug(f"Found meta section '{section_content}' for {article_url}")
                return section_content
            
            logger.debug(f"No <meta property='article:section'> found on {article_url}")
            return None

        except requests.RequestException as e:
            msg = traceback.format_exc()
            logger.error(msg)
            logger.warning(f"Network error fetching {article_url} for section meta: {e}")
            return None
        except Exception as e:
            msg = traceback.format_exc()
            logger.error(msg)
            logger.warning(f"Error parsing {article_url} for section meta: {e}")
            return None

    def _extract_section_from_page(self, soup) -> List[Dict[str, str]]:
        """
        페이지에서 기사 목록을 추출하고 목표 섹션 및 설정된 일자 (디폴트 최근 3일) 이내 기사만 필터링합니다.
        디일렉 웹사이트의 HTML 구조를 기반으로 기사를 찾습니다.
        """
        articles = []
        
        potential_article_elements = []
        article_rows = soup.find_all('div', class_="table-row")
        if article_rows:
            potential_article_elements.extend(article_rows)
        list_items = soup.find_all(['div', 'li'], class_=re.compile(r'article|list-item|news-item', re.I))
        if list_items:
            potential_article_elements.extend(list_items)

        seen_elements_text = set()
        unique_potential_articles = []
        for element in potential_article_elements:
            element_str = str(element)
            if element_str not in seen_elements_text:
                seen_elements_text.add(element_str)
                unique_potential_articles.append(element)
        
        logger.info(f"Total unique potential article elements to process: {len(unique_potential_articles)}")

        for element in unique_potential_articles:
            
            pattern_match = re.compile(r'^auto-article auto-*')
            # To-Do: 못찾는듯... 추가 조치 필요
            matching_divs_find_all = element.find_all('div', class_=pattern_match)
            # logger.info(f"{matching_divs_find_all}")
            if matching_divs_find_all:
                logger.debug(f"Skipping article (class name pattern is auto-article auto-*): {element.get_text()}")
                continue
            
            article_link = element.find('a', href=re.compile(r'/news/articleView\.html\?idxno=\d+'))
            
            if not article_link:
                if element.name == 'a' and re.match(r'/news/articleView\.html\?idxno=\d+', element.get('href', '')):
                    article_link = element
                else:
                    continue

            title = article_link.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            href = article_link.get('href')
            if href.startswith('/'):
                article_url = 'https://www.thelec.kr' + href
            else:
                article_url = href

            published_datetime = None
            date_text_from_list = self._extract_date_from_element(element)
            
            if date_text_from_list:
                published_datetime = self._parse_date(date_text_from_list)
            else:
                published_datetime = self._get_published_date_from_article_page(article_url)

            if not published_datetime:
                logger.debug(f"Skipping article (no date found after all attempts): {title}")
                continue

            # --- SECTION FILTERING LOGIC ---
            # If a target section is specified, perform filtering
            if self.target_section:
                # 1. Try to confirm section using existing text-based method (fast)
                is_section_match_from_list = self._is_target_section(element)

                # 2. If text-based check fails, try meta tag from article page (more reliable, but involves extra request)
                if not is_section_match_from_list:
                    meta_section = self._get_section_from_article_page(article_url)
                    
                    section_keywords_map = {
                        "반도체": ["반도체", "semiconductor", "S1N2"],
                        "디스플레이": ["디스플레이", "display"],
                        "IT‧게임": ["IT‧게임", "IT·게임", "IT게임"],
                        "방산‧에너지": ["방산‧에너지", "방산·에너지", "방산에너지"],
                        "중국산업동향": ["중국산업동향", "China Industry Trends"],
                        "배터리": ["배터리", "battery", "Battery"],
                        "자동차": ["자동차", "car", "automotive"]
                    }
                    target_keywords = section_keywords_map.get(self.target_section, [self.target_section])
                    
                    found_in_meta = False
                    if meta_section:
                        for keyword in target_keywords:
                            if keyword.lower() == meta_section.lower():
                                found_in_meta = True
                                break
                    
                    if found_in_meta:
                        logger.info(f"Section confirmed by meta tag for: {title} (Meta: '{meta_section}', Target: '{self.target_section}')")
                    else:
                        logger.debug(f"Skipping article (meta section mismatch or not found): {title} (Meta: '{meta_section}', Target: '{self.target_section}')")
                        continue # Skip if meta section doesn't match target or not found
                # If is_section_match_from_list was True, we proceed.
                # If is_section_match_from_list was False AND meta_section check failed, we continue (skip).
                # If is_section_match_from_list was False AND meta_section check passed, we proceed.

            # Check if the article is within from start_date to end_date
            published_datetime = kst_timezone.localize(published_datetime)
            if published_datetime >= self.start_date and published_datetime <= self.end_date:
                articles.append({
                    "title": title,
                    "url": article_url,
                    "published_date": published_datetime.strftime('%Y-%m-%d'),
                    "author": "",
                    "content": ""
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
        
        str_start_date = self.start_date.strftime('%Y-%m-%d')
        str_end_date = self.end_date.strftime('%Y-%m-%d')
        
        logger.info(f"Fetching articles from {self.base_url}")
        logger.info(f"Filtering for section: {self.target_section} published from {str_start_date} to {str_end_date}")

        for page in range(1, pages + 1):
            try:
                if 'page=' in self.base_url:
                    url = re.sub(r'page=\d+', f'page={page}', self.base_url)
                else:
                    url = self.base_url + (f"&page={page}" if "?" in self.base_url else f"?page={page}")
                
                logger.info(f"Fetching page {page}: {url}")
                
                self._update_headers()
                
                response = requests.get(url, headers=self.headers, timeout=15)
                response.raise_for_status()
                response.encoding = 'utf-8'
                soup = BeautifulSoup(response.text, 'html.parser')

                section_articles = self._extract_section_from_page(soup)
                news_list.extend(section_articles)
                
            except requests.RequestException as e:
                msg = traceback.format_exc()
                logger.error(msg)
                logger.error(f"Error fetching page {page}: {e}")
                continue
            except Exception as e:
                msg = traceback.format_exc()
                logger.error(msg)
                logger.error(f"Unexpected error on page {page}: {e}")
                continue

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
            self._update_headers()
            
            response = requests.get(article_url, headers=self.headers, timeout=15)
            response.raise_for_status()
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            content_selectors = [
                'div.article-content',
                'div.news-content', 
                'div.view-content',
                'div#article-content',
                'div.article_content',
                'div.articleContent',
                'div[id*="content"]',
                'div[class*="content"]',
                'div.user-content'
            ]

            content_div = None
            for selector in content_selectors:
                content_div = soup.select_one(selector)
                if content_div:
                    break

            if not content_div:
                content_div = soup.find('article') or soup.find('main')

            if not content_div:
                potential_content = soup.find_all('div', string=re.compile(r'.{100,}'))
                if potential_content:
                    content_div = max(potential_content, key=lambda x: len(x.get_text()))

            if content_div:
                for unwanted in content_div(["script", "style", "nav", "header", "footer", 
                                             "aside", "figure", "figcaption", "iframe", "img", "a"]):
                    unwanted.extract()

                for ad_class in ['ad', 'advertisement', 'related', 'recommend', 'popular', 'copyright']: 
                    for element in content_div.find_all(attrs={'class': re.compile(ad_class, re.I)}):
                        element.extract()

                for heading in content_div.find_all(['h2', 'h3', 'h4']):
                    heading_text = heading.get_text(strip=True)
                    if any(keyword in heading_text for keyword in ['관련기사', '관련 기사', '추천기사', '인기기사', '저작권']): 
                        current = heading
                        while current:
                            next_sibling = current.next_sibling
                            current.extract()
                            current = next_sibling
                        break

                paragraphs = content_div.find_all(['p', 'div', 'span'])
                content_parts = []
                for p in paragraphs:
                    text = p.get_text(separator=' ', strip=True)
                    if text and len(text) > 10:
                        content_parts.append(text)

                content = '\n'.join(content_parts)
                content = re.sub(r'\n\s*\n', '\n\n', content)
                
                logger.info(f"Successfully fetched content for {article_url}")
                return content.strip()
            else:
                logger.warning(f"Could not find article content for {article_url}")
                return "기사 내용을 찾을 수 없습니다."
                
        except requests.RequestException as e:
            msg = traceback.format_exc()
            logger.error(msg)
            logger.error(f"Network error fetching {article_url}: {e}")
            return f"네트워크 오류: {e}"
        except Exception as e:
            msg = traceback.format_exc()
            logger.error(msg)
            logger.error(f"Error parsing content from {article_url}: {e}")
            return f"파싱 오류: {e}"


# 로컬 테스트를 위한 예시 코드
if __name__ == "__main__":
    
    target_section = "반도체"
    
    section_url_dict = {
       "반도체": "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N2&view_type=sm",
       "자동차": "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N13&view_type=sm",
       "배터리": "https://www.thelec.kr/news/articleList.html?sc_section_code=S1N9&view_type=sm", 
    }
    
    target_section_en_dict = {
        "반도체": "semiconductor",
        "자동차": "automotive",
        "배터리": "battery",
    }
    
    target_section_en = target_section_en_dict[target_section]
    
    THELEC_URL = section_url_dict[target_section]
    
    # 반도체 섹션만 크롤링하도록 설정
    crawler = ThelecNewsCrawler(THELEC_URL, target_section=target_section)

    logger.info(f"--- Fetching recent semiconductor articles from {THELEC_URL} ---")
    articles = crawler.fetch_articles(pages=2)

    if articles:
        logger.info(f"Found {len(articles)} recent semiconductor articles.")
        for i, article in enumerate(articles):
            logger.info(f"\n--- Article {i + 1} ---")
            logger.info(f"Title: {article['title']}")
            logger.info(f"URL: {article['url']}")
            logger.info(f"Published Date: {article['published_date']}")        
            
            content = crawler.fetch_article_content(article['url'])
            logger.info(f"Content Snippet (first 300 chars): {content[:300]}...")
            article['content'] = content
            
    else:
        logger.warning("No recent semiconductor articles found or an error occurred.")

    # test_article_url = "https://www.thelec.kr/news/articleView.html?idxno=36117"
    # logger.info(f"\n--- Testing _get_published_date_from_article_page with: {test_article_url} ---")
    # published_date = crawler._get_published_date_from_article_page(test_article_url)
    # if published_date:
    #     logger.info(f"Extracted Published Date from article page: {published_date.strftime('%Y-%m-%d %H:%M')}")
    # else:
    #     logger.warning(f"Failed to extract published date from: {test_article_url}")

    # test_article_url_section = "https://www.thelec.kr/news/articleView.html?idxno=36493"
    # logger.info(f"\n--- Testing _get_section_from_article_page with: {test_article_url_section} ---")
    # extracted_section = crawler._get_section_from_article_page(test_article_url_section)
    # if extracted_section:
    #     logger.info(f"Extracted Section from article page: {extracted_section}")
    # else:
    #     logger.warning(f"Failed to extract section from: {test_article_url_section}")

    try:
        import json
        with open(f'{pjt_home_path}/data/thelec_{target_section_en}_articles.json', 'w', encoding='utf-8') as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)
        logger.info(f"Articles saved to thelec_{target_section_en}_articles.json")
    except ImportError:
        logger.info("JSON module not available, skipping file save.")
