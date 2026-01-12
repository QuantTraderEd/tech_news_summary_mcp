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
import undetected_chromedriver as uc

from fake_useragent import UserAgent
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

from src.services import gcs_upload_json

# --- 로거 설정 ---
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

kst_timezone = pytz.timezone('Asia/Seoul')

# --- 설정 ---
# 조회하고 싶은 트위터 사용자 아이디 목록을 리스트로 입력하세요.
TARGET_USERNAMES = [
    "rwang07",
    "MooreMorrisSemi",
    "insane_analyst",
    "BenBajarin",
    "OmerCheeema",
    "lithos_graphein",
    "dnystedt",
    "SKundojjala",
    "SemiAnalysis_",
    "semivision_tw",
    "artificialanlys",
    "kimmonismus",
    "scaling01",
    "danielnewmanUV",
    "The_AI_Investor",
    "SawyerMerritt",
    "wallstengine",
    "DrNHJ",
    ]
# 스크롤을 몇 번 내릴지 설정합니다. (숫자가 클수록 더 많은 게시글을 가져옵니다)
SCROLL_COUNT = 5
# 설정 파일 이름
CONFIG_FILE = f"{pjt_home_path}/config.json"

class TweetScraper:

    def __init__(self):
        self.driver = None
        self.wait = None
        self.actions = None
        self.end_date = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=24)
        self.start_date = self.end_date - dt.timedelta(days=2)

    def set_target_date_range(self, start_date: dt.datetime, end_date: dt.datetime):
        """
        tweet post 필터링 일자 설정 함수
        """
        self.start_date = start_date
        self.end_date = end_date
        logger.info(f"start_date=> {start_date}")
        logger.info(f"end_date=> {end_date}")

    def human_like_typing(self, element, text):
        """
        사람처럼 타이핑하는 함수
        """
        for char in text:
            element.send_keys(char)
            time.sleep(random.uniform(0.1, 0.3))  # 각 글자 사이 0.1~0.3초 랜덤 딜레이

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

    # --- 로그인 함수 ---
    def login_to_twitter(self, username: str, password: str, verification_info: str):
        """
        제공된 정보로 X(트위터)에 로그인합니다.
        """
        try:
            logger.info("로그인을 시작합니다...")
            self.driver.get("https://x.com/login")

            # 1. 사용자 이름/이메일 입력
            user_input = self.wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='text']")))
            # 입력창으로 마우스 이동 후 클릭
            self.actions.move_to_element(user_input).click().perform()
            # user_input.send_keys(username)
            self.human_like_typing(user_input, username)
            time.sleep(random.uniform(1.5, 2.2))

            # '다음' 버튼 클릭
            next_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Next')]")))
            self.actions.move_to_element(next_button).click().perform()
            logger.info("사용자 이름 입력 완료.")
            time.sleep(random.uniform(1.0, 2.0))

            # [수정됨] 사용자 이름 확인 / 전화번호,이메일 인증 / 비밀번호 입력의 동적 단계를 처리
            try:
                # 다음 단계가 무엇인지 확인: 비밀번호 필드 또는 다른 입력 필드
                next_input_xpath = "//input[@name='password'] | //input[@name='text']"
                next_input_element = self.wait.until(EC.presence_of_element_located((By.XPATH, next_input_xpath)))
                
                element_name = next_input_element.get_attribute("name")

                if element_name == 'password':
                    # 시나리오 1: 바로 비밀번호 입력 단계로 넘어간 경우
                    logger.info("비밀번호 입력 단계로 바로 진행합니다.")
                    password_input = next_input_element
                
                elif element_name == 'text':
                    # 시나리오 2 또는 3: 사용자 이름 재확인 또는 전화/이메일 인증 단계
                    page_text = self.driver.find_element(By.TAG_NAME, 'body').text
                    if "unusual login activity" in page_text or "phone number or email" in page_text:
                        # 시나리오 3: 비정상 로그인 활동으로 인한 추가 인증
                        logger.warning("비정상 로그인 활동 감지. 전화번호/이메일 인증을 시도합니다.")
                        if not verification_info:
                            logger.error("config.json에 'verification_info'가 필요하지만 설정되지 않았습니다.")
                            return False
                        next_input_element.send_keys(verification_info)
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Next')]"))).click()
                        logger.info(f"인증 정보(verification_info) 입력 완료!!!")
                    else:
                        # could not login in now.. try again later... 애러 발생 케이스
                        logger.error("could not login in now.. try again later...!!!!")
                        return False

                    # 위 단계를 거친 후, 최종적으로 비밀번호 입력창을 기다림
                    logger.info("비밀번호 필드를 기다립니다...")
                    password_input = self.wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='password']")))

            except TimeoutException:
                logger.error("로그인 다음 단계의 입력 필드를 찾을 수 없습니다. CAPTCHA 또는 예상치 못한 페이지일 수 있습니다.")
                error_page_filename = "login_error_page.html"
                with open(error_page_filename, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source)
                logger.error(f"현재 페이지 소스를 '{error_page_filename}' 파일로 저장했습니다. 파일을 열어 문제를 확인하세요.")
                return False
            

            # 2. 비밀번호 입력
            logger.info("비밀번호 입력 필드를 찾았습니다.")
            password_input.send_keys(password)

            # '로그인' 버튼 클릭
            logger.info("로그인 버튼을 찾고 있습니다...")
            login_button = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='로그인' or text()='Log in']//ancestor::button"))
            )
            logger.info("로그인 버튼 클릭!!")
            login_button.click()

            # UI가 완전히 안정화될 시간을 주기 위해 짧은 대기 시간을 추가합니다.
            time.sleep(3)

            # 3. 로그인 완료 확인
            self.wait.until(EC.presence_of_element_located((By.XPATH, "//a[@data-testid='AppTabBar_Home_Link']")))
            logger.info("로그인 성공!")
            return True

        except (TimeoutException, NoSuchElementException) as e:
            logger.error("로그인 중 오류가 발생했습니다. CSS 선택자 또는 페이지 구조가 변경되었을 수 있습니다.", exc_info=True)
            error_page_filename = f"{pjt_home_path}/login_error_page.html"
            with open(error_page_filename, "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logger.error(f"현재 페이지 소스를 '{error_page_filename}' 파일로 저장했습니다. 파일을 열어 문제를 확인하세요.")
            return False
        except Exception as e:
            logger.error(f"예상치 못한 오류 발생: {e}", exc_info=True)
            return False

    def login_to_tweeter(self):
        try:
            logger.info("X.com으로 이동합니다.")
            self.driver.get("https://x.com")
            time.sleep(3)

            with open(f"{pjt_home_path}/tweet_cookies.json", "r") as file:
                cookies = json.load(file)

            for cookie in cookies:
                # logger.info(cookie)

                if 'sameSite' in cookie:
                    if cookie['sameSite'] not in ['Strict', 'Lax', 'None']:
                        del cookie['sameSite']

                if 'expirationDate' in cookie:
                    cookie['expiry'] = int(cookie['expirationDate'])
                    del cookie['expirationDate']

                self.driver.add_cookie(cookie)

            logger.info("쿠키 정보를 브라우저에 적용했습니다.")

            self.driver.refresh()
            logger.info("페이지를 새로고침하여 로그인 상태를 확인합니다.")
            self.driver.get("https://x.com/home")
            time.sleep(random.uniform(3.5, 5.3))

        except Exception as e:
            logger.error(f"예상치 못한 오류 발생: {e}", exc_info=True)
            return False

        return True

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

    def scrape_user_post(self, target_username: str):
        """
        특정 사용자의 프로필 페이지로 이동하여 게시글을 수집하고 파일로 저장합니다.
        """

        # --- 프로필 페이지로 이동 ---
        logger.info("-" * 40)
        logger.info(f"[{target_username}] 님의 프로필 페이지를 엽니다...")
        profile_url = f"https://x.com/{target_username}"
        self.driver.get(profile_url)

        self.wait.until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='primaryColumn']")))
        logger.info(f"[{target_username}] 님의 프로필 페이지 로딩 완료!")

        # --- 프로필 정보 추출 및 출력 ---
        try:
            name_xpath = "//div[@data-testid='UserName']//span[1]//span[1]"
            name_element = self.wait.until(EC.presence_of_element_located((By.XPATH, name_xpath)))
            logger.info(f"이름: {name_element.text}")
        except (TimeoutException, NoSuchElementException):
            logger.warning("이름을 찾을 수 없습니다.")

        try:
            bio_elements = self.driver.find_elements(By.XPATH, "//div[@data-testid='UserDescription']")
            if bio_elements:
                logger.info(f"소개:\n{bio_elements[0].text}")
            else:
                logger.info("소개글이 없습니다.")
        except NoSuchElementException:
            logger.warning("소개글을 찾을 수 없습니다.")

        logger.info("타임라인 콘텐츠가 로드될 때까지 대기합니다...")
        self.wait.until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='cellInnerDiv']")))
        time.sleep(5)  # 스크롤 관련 스크립트가 완전히 초기화될 시간을 추가로 확보합니다.

        logger.info("게시글을 수집합니다...")
        posts_list = []
        processed_post_urls = set()

        for i in range(SCROLL_COUNT):
            logger.info(f"스크롤 {i + 1}/{SCROLL_COUNT} 진행 중...")
            articles = self.driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
            skip_article_cnt = 0
            for article in articles:
                timestamp = None
                post_url = None
                try:
                    # 게시글 URL을 포함하는 링크 요소 찾기
                    link_element = article.find_element(By.XPATH, ".//a[time]")
                    post_url = link_element.get_attribute('href')

                    # 이미 처리한 게시글은 건너뛰기
                    if post_url in processed_post_urls:
                        continue

                    # 기본 정보 추출
                    timestamp = link_element.find_element(By.TAG_NAME, "time").get_attribute('datetime')
                    post_text_element = article.find_element(By.XPATH, ".//div[@data-testid='tweetText']")
                    post_text = post_text_element.text
                    post_id = post_url.split('/')[-1]

                    created_at_time = self.parse_tweet_datetime(timestamp)
                    if created_at_time < self.start_date or created_at_time > self.end_date:
                        logger.info(f"skip {post_url}...  {timestamp} is outside target date...")
                        skip_article_cnt += 1
                        continue

                    # 긴 글 처리 로직
                    is_long_post = False
                    try:
                        article.find_element(By.XPATH, ".//span[text()='Show more' or text()='더 보기']")
                        is_long_post = True
                    except NoSuchElementException:
                        pass

                    if is_long_post:
                        logger.info(f" -> 긴 글을 발견했습니다. 전체 텍스트를 가져옵니다: {post_url}")
                        original_window = self.driver.current_window_handle
                        try:
                            self.driver.switch_to.new_window('tab')
                            self.driver.get(post_url)
                            full_text_xpath = "//article[@data-testid='tweet' and @tabindex='-1']//div[@data-testid='tweetText']"
                            full_text_element = self.wait.until(EC.presence_of_element_located((By.XPATH, full_text_xpath)))
                            post_text = full_text_element.text
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
                    posts_list.append(post_data)
                    processed_post_urls.add(post_url)

                except (NoSuchElementException, StaleElementReferenceException):
                    # msg = traceback.format_exc()
                    msg = 'chk traceback....'
                    logger.warning(f'find_element 함수에 의해서 요소를 찾을 수 없음. ==>\n{msg}')
                    if post_url is not None and timestamp is not None:
                        logger.warning(f"plz checkup post: {post_url} | {timestamp}")
                    continue                

            # 페이지 맨 아래로 스크롤하여 새 게시글을 로드합니다.
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            # 새 게시글이 로드될 때까지 잠시 기다립니다.
            time.sleep(random.uniform(3.7, 5.5))

            if skip_article_cnt >= 4:
                logger.info(f"skip_article_cnt ==> {skip_article_cnt}")
                logger.info("stop scroll for skip out-date post!!")
                break

        # --- 결과 출력 및 파일 저장 ---
        if posts_list:
            # 수집된 게시글 목록 출력
            for i, post in enumerate(posts_list):
                msg = post['text'][:50].replace('\n', ' ')
                logger.info(f"[{i + 1}]")
                logger.info(f"  URL: {post['url']}")
                logger.info(f"  작성 시간: {post['created_at']}")
                logger.info(f"  내용(50글자):{msg}")
                logger.info("-" * 20)

            logger.info(f"[{target_username}] 님으로부터 총 {len(posts_list)}개의 게시글을 수집했습니다.")
            output_filename = f"{pjt_home_path}/data/{target_username}_posts.json"
            data_to_save = {"data": posts_list}
            # JSON 파일로 저장하는 부분
            try:
                with open(output_filename, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, ensure_ascii=False, indent=4)
                logger.info(f"-> 게시글을 '{output_filename}' 파일로 성공적으로 저장했습니다.")
            except Exception as e:
                logger.warning(f"-> '{output_filename}' 파일 저장 중 오류 발생: {e}", exc_info=True)
        else:
            logger.warning(f"[{target_username}] 님의 게시글을 수집하지 못했습니다.")
            
    def upload_posts_json_to_gcs(self, target_username: str, base_ymd: str):
        local_data_dir = f'{pjt_home_path}/data'
        filename = f"{target_username}_posts.json"
        try:        
            
            local_file_path = os.path.join(local_data_dir, filename)
            gcs_upload_json.upload_local_file_to_gcs(local_file_path, date_str=base_ymd)
            
        except Exception as e:            
            logger.error(f"{filename} 업로드 중 오류 발생: {e}", exc_info=True)


def main(base_ymd: str, posts_json_upload: bool = False):
    """
    tweet post 수집의 메인 실행 함수.
    :param str base_ymd: post 수집 기준 일자 (yyyymmdd)
    :param bool posts_json_upload: posts.json 파일 GCS 업로드 여부
    """
    tweet_scraper = None
    try:
        # 날짜 범위 설정
        end_date = dt.datetime.strptime(base_ymd, "%Y%m%d")
        end_date = end_date.replace(tzinfo=dt.timezone.utc) + dt.timedelta(hours=24)
        # start_date = end_date - dt.timedelta(days=2)
        now_utc_dt = dt.datetime.now(dt.timezone.utc)
        start_date = now_utc_dt - dt.timedelta(hours=18)
        start_date = start_date.replace(tzinfo=dt.timezone.utc)
        
        if start_date > end_date:
            logger.error("start_date > end_date ... ! plz check up end_date!!")
            raise Exception

        # --- 설정 파일 로드 ---
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            login_username = config['TWEET_USERNAME']
            login_password = config['TWEET_PASSWORD']
            verification_info = config['VERIFICATION_INFO']
        except FileNotFoundError:
            logger.error(f"오류: 설정 파일({CONFIG_FILE})을 찾을 수 없습니다.")
            sys.exit(1)  # 프로그램 종료
        except KeyError:
            logger.error(f"오류: {CONFIG_FILE}에 'username' 또는 'password' 또는 'verification_info' 키가 없습니다.")
            sys.exit(1)  # 프로그램 종료

        # scraper 설정
        tweet_scraper = TweetScraper()
        tweet_scraper.set_target_date_range(start_date, end_date)
        tweet_scraper.set_webdriver()

        if not tweet_scraper.login_to_tweeter():
            raise Exception("로그인에 실패하여 스크립트를 중단합니다.")

        # --- 지정된 모든 사용자에 대해 스크래핑 실행 ---
        for user in TARGET_USERNAMES:
            tweet_scraper.scrape_user_post(user)
            if posts_json_upload:
                tweet_scraper.upload_posts_json_to_gcs(user, base_ymd)

        # 모든 작업이 끝나면 브라우저 종료
        if tweet_scraper.driver: tweet_scraper.driver.quit()
        logger.info("\n모든 작업이 완료되어 스크립트를 종료합니다.")

    except Exception as e:
        # 브라우저 종료
        if tweet_scraper:
            if tweet_scraper.driver: tweet_scraper.driver.quit()
        logger.error("스크립트 실행 중 처리되지 않은 예외 발생", exc_info=True)
        sys.exit(1)

# --- 메인 실행 부분 ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="tweet post 수집 메인 배치 함수"
    )

    # base_ymd 인자 추가
    parser.add_argument(
        "base_ymd",
        type=str,
        default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"),  # 기본값은 현재 날짜 (UTC 기준)
        help="post 수집 기준 일자 (yyyymmdd), 미입력 시 현재 날짜가 기본값",
        nargs='?'
    )

    args = parser.parse_args()

    # base_ymd 유효성 검증
    try:
        dt.datetime.strptime(args.base_ymd, "%Y%m%d")
    except ValueError:
        parser.error(f"잘못된 날짜 형식입니다: {args.base_ymd}. yyyymmdd 형식으로 입력해주세요.")

    main(base_ymd=args.base_ymd, posts_json_upload=False)
