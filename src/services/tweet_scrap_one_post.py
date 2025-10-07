import os
import sys
import site
import traceback
import logging
import time
import json
import random
import datetime as dt

import pytz

from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (TimeoutException,
                                        NoSuchElementException,
                                        StaleElementReferenceException,
                                        WebDriverException,
                                        ElementClickInterceptedException)

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# --- 로거 설정 ---
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')

class TweetScrapOnePost:

    def __init__(self):
        self.driver = None
        self.wait = None
        self.actions = None

    def set_webdriver(self):
        # --- 드라이버 설정 및 로그인 (최초 한 번만 실행) ---
        logger.info("웹 드라이버를 설정합니다...")
        service = Service(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new") # 브라우저를 숨기기
        options.add_argument("--window-size=1920,1080")  # 충분한 창 크기 설정
        options.add_argument("--no-sandbox") # Sandbox 프로세스 사용 안함: Docker 컨테이너와 같은 제한된 환경에서 필요
        options.add_argument("--disable-dev-shm-usage") # /dev/shm 파티션 사용 안함: 일부 Docker 환경에서 메모리 부족 오류 방지
        options.add_argument("--disable-gpu") # GPU 가속 비활성화
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        # 크롬 최신 버전으로 유지 필요 - 추후 fake-agent 활용 검토 필요
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, 15)
        self.actions = ActionChains(self.driver)

    def parse_tweet_datetime(self, datetime_str: str) -> dt.datetime:
        """
        Tweet 날짜시간 문자열을 datetime 객체로 파싱합니다.
        Tweet 날짜 형식: YYYY-MM-DDTHH:MM:SS.Z  (예: 2025-06-03T22:14:35.000Z)
        """
        try:
            # 'Z'를 '+00:00'으로 변환
            datetime_str = datetime_str.replace('Z', '+00:00')
            # datetime 객체로 파싱
            result = dt.datetime.fromisoformat(datetime_str)
            return result
        except ValueError as e:
            logger.error(f"Failed to parse date string '{datetime_str}': {e}", exc_info=True)
            return dt.datetime.min  # 파싱 실패 시 매우 오래된 날짜 반환하여 필터링되도록 함

    def scrap_one_post(self, post_url: str):
        """
        특정 URL 게시글을 수집하고 파일로 저장합니다.
        """
        post_id = post_url.split('/')[-1]
        timestamp = "to-do"
        original_window = self.driver.current_window_handle
        try:
            self.driver.switch_to.new_window('tab')
            self.driver.get(post_url)

            full_text_xpath = "//article[@data-testid='tweet' and @tabindex='-1']//div[@data-testid='tweetText']"
            full_text_element = self.wait.until(EC.presence_of_element_located((By.XPATH, full_text_xpath)))
            post_text = full_text_element.text

            time_element_xpath = "//span[contains(text(), 'Last edited')]/following-sibling::a//time"
            timestamp = self.driver.find_element(By.TAG_NAME, "time").get_attribute('datetime')
            last_edited_time = self.parse_tweet_datetime(timestamp)

            time.sleep(2)

        except TimeoutException:
            # msg = traceback.format_exc()
            msg = 'chk traceback....'
            logger.warning(f'full_text_element is timeout!! ==>\n{msg}')
            logger.warning(f"plz checkup post: {post_url} | {timestamp}")
            post_text = 'ft timeout'
        finally:
            self.driver.close()
            self.driver.switch_to.window(original_window)

        # 수집한 정보를 딕셔너리로 묶어 리스트에 추가
        post_data = {
            'url': post_url,
            'id': post_id,
            'created_at': timestamp,
            'text': post_text
        }

        return post_data

def main():
    # scraper 설정
    tweet_scraper = TweetScrapOnePost()
    tweet_scraper.set_webdriver()

    # post_url_list = [
    #     'https://x.com/ShanuMathew93/status/1975211850884100493',
    #     'https://x.com/LisaSu/status/1975210493796385233',
    # ]

    input_filename = pjt_home_path + '/data/manual_post_urls.json'
    logger.info(f"load post urls from {input_filename} ...")
    with open(input_filename, 'r', encoding='utf-8') as f:
        post_url_list = json.load(f)
        logger.info(f"post_url_list cnt => {len(post_url_list)}")

    post_data_list = list()
    for post_url in post_url_list:
        post_data = tweet_scraper.scrap_one_post(post_url)
        logger.info(post_data)
        post_data_list.append(post_data)


    posts_data = {"data": post_data_list}

    output_filename = os.path.join(pjt_home_path, 'data', 'tweet_agg_one_posts.json')

    with open(output_filename, 'w', encoding='utf-8') as f:
        json.dump(posts_data, f, ensure_ascii=False, indent=4)

if __name__ == "__main__":
    main()

