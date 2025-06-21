import os
import sys
import site
import traceback
import time
import json

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

# --- 설정 ---
# 조회하고 싶은 트위터 사용자의 아이디를 여기에 입력하세요.
TARGET_USERNAME = "The_AI_Investor"  # rwang07, wallstengine
# 스크롤을 몇 번 내릴지 설정합니다. (숫자가 클수록 더 많은 게시글을 가져옵니다)
SCROLL_COUNT = 5
# 설정 파일 이름
CONFIG_FILE = "config.json"


# --- 로그인 함수 ---
def login_to_twitter(driver, wait, username, password):
    """
    제공된 정보로 X(트위터)에 로그인합니다.
    """
    try:
        print("로그인을 시작합니다...")
        driver.get("https://x.com/login")

        # 1. 사용자 이름/이메일 입력
        user_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='text']")))
        user_input.send_keys(username)

        # '다음' 버튼 클릭
        next_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[contains(text(), 'Next')]")))
        next_button.click()
        print("사용자 이름 입력 완료.")

        # 2. 비밀번호 입력
        # 사용자 이름이 특이한 경우, 사용자 이름을 다시 확인하는 단계가 있을 수 있습니다.
        # 비밀번호 필드가 나타날 때까지 기다립니다.
        password_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='password']")))
        password_input.send_keys(password)

        # '로그인' 버튼 클릭
        # [수정됨] 일반적인 click이 실패할 경우를 대비해, 더 안정적인 JavaScript 클릭을 사용하도록 변경합니다.
        print("로그인 버튼을 찾고 있습니다...")
        login_button = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='로그인' or text()='Log in']//ancestor::button"))
        )
        print("로그인 버튼 클릭!!")
        login_button.click()

        # UI가 완전히 안정화될 시간을 주기 위해 짧은 대기 시간을 추가합니다.
        time.sleep(3)

        # 3. 로그인 완료 확인
        # 메인 피드의 '홈' 탭이 보일 때까지 기다립니다.
        print("로그인 확인 중...")
        wait.until(EC.presence_of_element_located((By.XPATH, "//a[@data-testid='AppTabBar_Home_Link']")))
        print("로그인 성공!")
        return True

    except (TimeoutException, NoSuchElementException) as e:
        print("로그인 중 오류가 발생했습니다. CSS 선택자 또는 페이지 구조가 변경되었을 수 있습니다.")
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"예상치 못한 오류 발생: {e}")
        traceback.print_exc()
        return False


# --- 스크립트 시작 ---

def main(username):
    """
    지정된 트위터 사용자의 프로필 페이지를 열고, 프로필 정보와 게시글 목록(URL, 시간 포함)을 출력합니다.

    Args:
        username (str): 조회할 트위터 사용자 아이디
    """
    driver = None
    try:
        # --- 설정 파일 로드 ---
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
            login_username = config['tweet_username']
            login_password = config['tweet_password']
        except FileNotFoundError:
            print(f"오류: 설정 파일({CONFIG_FILE})을 찾을 수 없습니다. 스크립트와 같은 위치에 파일을 생성해주세요.")
            return
        except KeyError:
            print(f"오류: {CONFIG_FILE}에 'username' 또는 'password' 키가 없습니다.")
            return

        # --- 드라이버 설정 및 로그인 ---
        service = Service(ChromeDriverManager().install())
        options = webdriver.ChromeOptions()
        # options.add_argument("--headless") # 브라우저를 숨기고 싶을 때 주석 해제
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        driver = webdriver.Chrome(service=service, options=options)
        wait = WebDriverWait(driver, 15)

        if not login_to_twitter(driver, wait, login_username, login_password):
            raise Exception("로그인에 실패하여 스크립트를 중단합니다.")

        # --- 프로필 페이지로 이동 ---
        target_username = username
        print("-" * 30)
        print(f"'{target_username}' 님의 프로필 페이지를 엽니다...")
        profile_url = f"https://x.com/{target_username}"
        driver.get(profile_url)

        # 페이지 로딩 확인
        wait.until(EC.presence_of_element_located((By.XPATH, f"//a[@href='/{target_username}/header_photo']")))
        print("프로필 페이지 로딩 완료!")
        print("-" * 30)

        # --- 프로필 정보 추출 및 출력 ---
        try:
            name_xpath = "//div[@data-testid='UserName']//span[1]//span[1]"
            name_element = wait.until(EC.presence_of_element_located((By.XPATH, name_xpath)))
            print(f"이름: {name_element.text}")
        except (TimeoutException, NoSuchElementException):
            print("이름을 찾을 수 없습니다.")

        try:
            bio_elements = driver.find_elements(By.XPATH, "//div[@data-testid='UserDescription']")
            if bio_elements:
                print(f"소개:\n{bio_elements[0].text}")
            else:
                print("소개글이 없습니다.")
        except NoSuchElementException:
            print("소개글을 찾을 수 없습니다.")

        print("-" * 30)
        print("게시글을 수집합니다...")

        # --- 게시글 목록 수집 ---
        posts_list = []
        # 중복 수집을 방지하기 위해 게시글 URL을 저장하는 set
        processed_post_urls = set()

        last_height = driver.execute_script("return document.body.scrollHeight")

        for _ in range(SCROLL_COUNT):
            # 각 게시글은 'article' 태그로 감싸여 있습니다.
            articles = driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")

            for article in articles:
                try:
                    # 게시글의 고유 링크와 시간을 포함하는 a 태그를 찾습니다.
                    link_element = article.find_element(By.XPATH, ".//a[time]")
                    post_url = link_element.get_attribute('href')

                    # 이미 수집한 게시글이면 건너뜁니다.
                    if post_url in processed_post_urls:
                        continue

                    # 기본 정보 추출
                    timestamp = link_element.find_element(By.TAG_NAME, "time").get_attribute('datetime')
                    post_text_element = article.find_element(By.XPATH, ".//div[@data-testid='tweetText']")
                    post_text = post_text_element.text
                    post_id = post_url.split('/')[-1]

                    # 긴 글 처리 로직
                    # 게시글 내부에 'Show more' 또는 '더 보기' 텍스트가 있는지 확인
                    is_long_post = False
                    try:
                        # 영어(Show more) 또는 한국어(더 보기) 텍스트를 가진 요소를 찾습니다.
                        article.find_element(By.XPATH, ".//span[text()='Show more' or text()='더 보기']")
                        is_long_post = True
                    except NoSuchElementException:
                        # 해당 요소가 없으면 긴 글이 아닌 것으로 간주합니다.
                        pass

                    if is_long_post:
                        print(f"긴 글을 발견했습니다 ('Show more'/'더 보기' 감지). 전체 텍스트를 가져옵니다: {post_url}")
                        original_window = driver.current_window_handle

                        try:
                            # 새 탭에서 게시글 URL 열기
                            driver.switch_to.new_window('tab')
                            driver.get(post_url)

                            # 상세 페이지의 메인 트윗을 명확하게 타겟팅하여 전체 텍스트 로딩 대기
                            full_text_xpath = "//article[@data-testid='tweet' and @tabindex='-1']//div[@data-testid='tweetText']"
                            full_text_element = wait.until(EC.presence_of_element_located((By.XPATH, full_text_xpath)))

                            time.sleep(3)

                            # 전체 텍스트 추출 후, 기존 post_text 변수를 덮어쓰기
                            post_text = full_text_element.text
                            print(" -> 전체 텍스트를 성공적으로 가져왔습니다.")

                        except Exception as e:
                            print(f" -> 전체 텍스트를 가져오는 중 오류 발생: {e}. 타임라인의 일부 텍스트를 사용합니다.")
                        finally:
                            # 새 탭 닫고 원래 탭으로 돌아오기
                            driver.close()
                            driver.switch_to.window(original_window)

                    # 수집한 정보를 딕셔너리로 묶어 리스트에 추가
                    post_data = {
                        'url': post_url,
                        'id': post_id,
                        'created_at': timestamp,
                        'text': post_text
                    }
                    posts_list.append(post_data)
                    processed_post_urls.add(post_url)

                except NoSuchElementException:
                    # 광고나 다른 형식의 article은 건너뜁니다.
                    continue

            # 페이지 맨 아래로 스크롤하여 새 게시글을 로드합니다.
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            # 새 게시글이 로드될 때까지 잠시 기다립니다.
            time.sleep(4)

        if posts_list:
            print(f"총 {len(posts_list)}개의 게시글을 수집했습니다.")

            # [수정됨] JSON 파일로 저장하는 부분
            output_filename = f"{target_username}_posts.json"
            data_to_save = {"data": posts_list}
            try:
                with open(output_filename, 'w', encoding='utf-8') as f:
                    json.dump(data_to_save, f, ensure_ascii=False, indent=4)
                print(f"게시글을 '{output_filename}' 파일로 성공적으로 저장했습니다.")
            except Exception as e:
                print(f"'{output_filename}' 파일 저장 중 오류 발생: {e}")

            print("-" * 30)
            # 수집된 게시글 목록 출력
            for i, post in enumerate(posts_list):
                msg = post['text'][:50].replace('\n', ' ')
                print(f"[{i + 1}]")
                print(f"  URL: {post['url']}")
                print(f"  작성 시간: {post['created_at']}")
                print(f"  내용(50글자):{msg}")
                print("-" * 20)
        else:
            print("게시글을 수집하지 못했습니다.")

        print("-" * 30)

    except Exception as e:
        print(f"오류가 발생했습니다: {e}")
        traceback.print_exc()

    finally:
        if driver:
            driver.quit()
        print("스크립트를 종료합니다.")


# --- 메인 실행 부분 ---
if __name__ == "__main__":
    # pip install selenium webdriver-manager
    main(TARGET_USERNAME)
