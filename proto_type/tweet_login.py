import os
import sys
import site
import traceback
import logging
import time
import json
import random

import undetected_chromedriver as uc

from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# --- 설정 ---
# Chrome 프로필이 저장될 경로를 지정합니다.
# 'C:/Users/YourUsername/AppData/Local/Google/Chrome/User Data' 와 같이 실제 경로를 지정해도 됩니다.
# 여기서는 스크립트와 같은 폴더에 'chrome_profile' 이라는 폴더를 생성하여 사용합니다.
# PROFILE_PATH = "/Users/assa/Library/Application Support/Google/Chrome"
# 설정 파일 이름
CONFIG_FILE = f"{pjt_home_path}/config.json"
# ----------

# --- 로거 설정 ---
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s',
                              datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

options = uc.ChromeOptions()
# options = webdriver.ChromeOptions()
# options.add_argument(f"--user-data-dir={PROFILE_PATH}") # user-data-dir 옵션으로 프로필 경로를 지정
# options.add_argument("--headless=new")  # 브라우저를 숨기기
options.add_argument("--start-maximized")
options.add_argument("--lang=en-US,en")
options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                     "Chrome/138.0.0.0 Safari/537.36")
options.add_argument("--no-sandbox")  # Sandbox 프로세스 사용 안함: Docker 컨테이너와 같은 제한된 환경에서 필요
options.add_argument("--disable-dev-shm-usage")  # /dev/shm 파티션 사용 안함: 일부 Docker 환경에서 메모리 부족 오류 방지
options.add_argument("--disable-gpu")  # GPU 가속 비활성화
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)

# 드라이버 실행
# service = Service(ChromeDriverManager().install())
# driver = webdriver.Chrome(service=service, options=options)
driver = uc.Chrome(options=options, version_main=138)
wait = WebDriverWait(driver, 15)

logger.info(f"chromedirver path => {service.path}")

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)
username = config['TWEET_USERNAME']
login_password = config['TWEET_PASSWORD']
verification_info = config['VERIFICATION_INFO']

try:
    # 프로필을 사용하면 보통 바로 로그인된 x.com 메인 페이지로 이동할 수 있습니다.
    driver.get("https://x.com/login")
    logger.info("X.com 메인 페이지로 이동합니다. 로그인 상태를 확인하세요.")
    # print("만약 로그인이 안 되어 있다면, 지금 수동으로 로그인해주세요.")
    # print("로그인 완료 후 30초간 대기합니다...")
    # # 수동으로 로그인하거나 다음 작업을 수행할 시간을 줍니다.

    # 1. 사용자 이름/이메일 입력
    user_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='text']")))
    user_input.send_keys(username)
    time.sleep(random.uniform(1.5, 2.2))

    # '다음' 버튼 클릭
    next_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Next')]")))
    next_button.click()
    logger.info("사용자 이름 입력 완료.")
    time.sleep(random.uniform(2.0, 3.0))

    # 다음 단계가 무엇인지 확인: 비밀번호 필드 또는 다른 입력 필드
    next_input_xpath = "//input[@name='password'] | //input[@name='text']"
    next_input_element = wait.until(EC.presence_of_element_located((By.XPATH, next_input_xpath)))

    element_name = next_input_element.get_attribute("name")

    if element_name == 'password':
        # 시나리오 1: 바로 비밀번호 입력 단계로 넘어간 경우
        logger.info("비밀번호 입력 단계로 바로 진행합니다.")
        password_input = next_input_element

    elif element_name == 'text':
        # 시나리오 2 또는 3: 사용자 이름 재확인 또는 전화/이메일 인증 단계
        page_text = driver.find_element(By.TAG_NAME, 'body').text
        if "unusual login activity" in page_text or "phone number or email" in page_text:
            # 시나리오 3: 비정상 로그인 활동으로 인한 추가 인증
            logger.warning("비정상 로그인 활동 감지. 전화번호/이메일 인증을 시도합니다.")

            next_input_element.send_keys(verification_info)
            wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Next')]"))).click()
            logger.info(f"인증 정보(verification_info) 입력 완료!!!")
        else:
            raise Exception("could not login in now.. try again later...")

        # 위 단계를 거친 후, 최종적으로 비밀번호 입력창을 기다림
        logger.info("비밀번호 필드를 기다립니다...")
        password_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='password']")))


except Exception as e:
    logger.error(f"오류 발생: {e}")
    error_page_filename = f"{pjt_home_path}/login_error_page.html"
    with open(error_page_filename, "w", encoding="utf-8") as f:
        f.write(driver.page_source)
    logger.error(f"현재 페이지 소스를 '{error_page_filename}' 파일로 저장했습니다. 파일을 열어 문제를 확인하세요.")

finally:
    driver.quit()
    logger.info("브라우저를 종료합니다.")