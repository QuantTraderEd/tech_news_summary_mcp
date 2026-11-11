import os
import sys
import site
import logging
import json
import datetime as dt

import pytz
import pandas as pd

from fastapi import FastAPI, HTTPException, Response
from fastapi import BackgroundTasks
from pydantic import BaseModel, Field

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services import gcs_upload_json
from src.services import gcs_download_json

from src.services import news_crawler_thelec
from src.services import news_crawler_zdnet
from src.services import news_crawler_etnews

from src.services import news_summarizer
from src.services import send_mail

from src.services import tweet_scrapper_post
from src.services import tweet_summarizer
from src.services import tweet_daily_dup_check
from src.services import send_mail_tweet

kst_timezone = pytz.timezone('Asia/Seoul')

# 로깅 설정
logger = logging.getLogger(__file__)
formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(filename)s %(lineno)d: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger.setLevel(logging.INFO)
stream_log = logging.StreamHandler(sys.stdout)
stream_log.setFormatter(formatter)
logger.addHandler(stream_log)

app = FastAPI(
    title="Tech News Summary Notifier",
    description="",
    version="1.0.0"
)

# Pydantic 모델을 사용하여 요청 본문 유효성 검사
class BatchParams(BaseModel):
    batch_type: str
    params: dict = Field(default_factory=dict)

@app.get("/")
async def health_check():
    return "Tech News Summary (FASTAPI) Good!!"

@app.post("/execute-batch")
async def execute_batch(backgroundtasks: BackgroundTasks, payload: BatchParams):
    """
    워커 엔드포인트.
    실제 오래 걸리는 배치 작업을 background 수행
    """
    
    batch_type = payload.batch_type
    params = payload.params
    logger.info(f"Worker received task for batch_type: {batch_type} with params: {params}")
    
    try:
        if batch_type == 'news':
            backgroundtasks.add_task(run_news_batch)
        elif batch_type == 'tweet':
            backgroundtasks.add_task(run_tweet_batch)
        elif batch_type == 'tweet_2nd':
            backgroundtasks.add_task(run_tweet_2nd_batch)
        elif batch_type == 'tweet_rerun':
            base_ymd = params.get('base_ymd', None)
            backgroundtasks.add_task(run_tweet_rerun_batch, base_ymd)
        elif batch_type == "tweet_single_user":
            base_ymd = params.get('base_ymd', None)
            tweet_username = params.get('tweet_username', None)
            backgroundtasks.add_task(run_tweet_single_user_batch, base_ymd, tweet_username)
        elif batch_type == "tweet_daily_dup":
            base_ymd = params.get('base_ymd', None)
            mode = params.get('mode', 'default')
            backgroundtasks.add_task(run_tweet_daily_dup_batch, base_ymd, mode)
        else:
            msg = f"Error: Unknown batch type '{batch_type}'."
            logger.warning(msg)
            # Cloud Tasks가 재시도하지 않도록 성공(2xx) 응답을 반환하는 것이 중요
            return Response(status_code=204)

        logger.info(f"Successfully run batch.. batch will run background..: {batch_type}")
        return Response(status_code=200) # 성공적으로 완료되면 200 OK
    except Exception as e:
        # 예외 발생 시, Cloud Tasks가 재시도하도록 에러(5xx) 응답을 반환
        logger.info(f"Error during batch execution: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Batch job failed: {e}")
        

def run_news_batch():
    base_ymd = dt.datetime.now(kst_timezone).strftime("%Y%m%d")
    
    news_crawler_zdnet.main('반도체', base_ymd)
    news_crawler_zdnet.main('자동차', base_ymd)
    news_crawler_zdnet.main('배터리', base_ymd)
    news_crawler_zdnet.main('컴퓨팅', base_ymd)
    
    news_crawler_thelec.main('반도체', base_ymd)
    news_crawler_thelec.main('자동차', base_ymd)
    news_crawler_thelec.main('배터리', base_ymd)
    
    news_crawler_etnews.main('전자', base_ymd)
    news_crawler_etnews.main('SW', base_ymd)
    news_crawler_etnews.main('IT', base_ymd)
    
    gcs_upload_json.main('zdnet', base_ymd)
    gcs_upload_json.main('thelec', base_ymd)
    gcs_upload_json.main('etnews', base_ymd)
    
    news_summarizer.main(base_ymd)
    
    pwd = os.environ.get('NVR_MAIL_PWD')
    send_mail.main(pwd)
    
def run_tweet_batch():
    base_ymd = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")  # 기본값은 현재 날짜 (UTC 기준)

    gcs_download_json.download_gcs_to_local(file_name='tweet_cookies.json',
                                            bucket_name='gcs-private-pjt-data',
                                            gcs_base_path='config',
                                            date_str='20000101',
                                            local_file_path=pjt_home_path)
    
    tweet_scrapper_post.main(base_ymd, True)
    tweet_summarizer.main(base_ymd)

    # 임시 메일링 중단
    # pwd = os.environ.get('NVR_MAIL_PWD')
    # send_mail_tweet.main(pwd, base_ymd)

    count_tweet_posts()

def run_tweet_2nd_batch():
    base_ymd = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")  # 기본값은 현재 날짜 (UTC 기준)

    gcs_download_json.download_gcs_to_local(file_name='tweet_cookies.json',
                                            bucket_name='gcs-private-pjt-data',
                                            gcs_base_path='config',
                                            date_str='20000101',
                                            local_file_path=pjt_home_path)

    tweet_scrapper_post.main(base_ymd, True, tweet_usernames= tweet_scrapper_post.TARGET_USERNAMES_2ND)
    tweet_summarizer.main(base_ymd, tweet_usernames= tweet_scrapper_post.TARGET_USERNAMES_2ND)

    # 임시 메일링 중단
    # pwd = os.environ.get('NVR_MAIL_PWD')
    # send_mail_tweet.main(pwd, base_ymd)

    count_tweet_posts(tweet_usernames=tweet_scrapper_post.TARGET_USERNAMES_2ND)
    
def run_tweet_rerun_batch(base_ymd=None):

    if not base_ymd:
        base_ymd = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")  # 기본값은 현재 날짜 (UTC 기준)

    gcs_download_json.download_gcs_posts_json_to_local(target_date=base_ymd)
    tweet_summarizer.main(base_ymd)

    # 임시 메일링 중단
    # pwd = os.environ.get('NVR_MAIL_PWD')
    # send_mail_tweet.main(pwd, base_ymd)

    count_tweet_posts()
    
def run_tweet_single_user_batch(base_ymd=None, tweet_username=None):
    
    if not base_ymd:
        base_ymd = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")  # 기본값은 현재 날짜 (UTC 기준)
        
    gcs_download_json.download_gcs_to_local(file_name='tweet_cookies.json',
                                            bucket_name='gcs-private-pjt-data',
                                            gcs_base_path='config',
                                            date_str='20000101',
                                            local_file_path=pjt_home_path)
    
    tweet_scrapper_post.main(base_ymd, True, tweet_username)
    
def run_tweet_daily_dup_batch(base_ymd=None, mode='default'):
    
    if not base_ymd:
        base_ymd = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y%m%d")  # 기본값은 현재 날짜 - 1 day  (UTC 기준)
        
    tweet_daily_dup_check.main(base_ymd, mode)
        
    pwd = os.environ.get('NVR_MAIL_PWD')
    send_mail_tweet.main(pwd, base_ymd)

    logger.info(f"tweet_daily_dup_batch for {base_ymd} Done!!")


def count_tweet_posts(tweet_usernames: list = None):
    """
    로컬 data 디렉토리에 저장된 파일을 기준으로 카운트합니다.
    :param list tweet_usernames: 수집 대상 트위터 사용자 아이디 목록
    """

    # 데이터가 저장된 기본 경로를 설정합니다.
    data_path = os.path.join(pjt_home_path, 'data')

    # tweet_scrapper_post 모듈에서 타겟 사용자 리스트를 가져옵니다.
    if tweet_usernames is None:
        target_users = tweet_scrapper_post.TARGET_USERNAMES
    else:
        target_users = tweet_usernames

    post_counts = {}
    logger.info(f"Counting tweet posts...")

    for username in target_users:
        file_path = os.path.join(data_path, f"{username}_posts.json")
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                posts = json.load(f)
                # JSON 파일 안의 리스트 길이를 세어 게시글 수를 계산합니다.
                post_counts[username] = len(posts['data'])
                logger.info(f"Found {len(posts['data'])} posts for user '{username}' in {file_path}")
        except FileNotFoundError:
            logger.warning(f"File not found for user '{username}': {file_path}")
            post_counts[username] = 0  # 파일이 없으면 0개로 처리
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON for user '{username}': {file_path}")
            post_counts[username] = "Error: Invalid JSON file"
        except Exception as e:
            logger.error(f"An unexpected error occurred for user '{username}': {e}", exc_info=True)
            post_counts[username] = f"Error: {e}"

    df_post_cnt = pd.DataFrame([post_counts]).T
    df_post_cnt.columns = ['cnt']
    logger.info(f"post_counts ==> \n {df_post_cnt.to_markdown()}")

    return df_post_cnt
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
