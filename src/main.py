import os
import sys
import site
import logging
import time
import asyncio
import datetime as dt

import pytz

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi import BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
# from typing import List
from google.protobuf import timestamp_pb2, duration_pb2

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

from src.services import gcs_upload_json
from src.services import gcs_download_json

from src.services import news_crawler_thelec
from src.services import news_crawler_zdnet
from src.services import news_summarizer
from src.services import send_mail

from src.services import tweet_scrapper_post
from src.services import tweet_summarizer
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

# GCP 프로젝트 정보 및 Cloud Tasks 큐 정보
PROJECT_ID = os.environ.get('GCP_PROJECT_ID', 'theta-window-364015')
LOCATION_ID = os.environ.get('GCP_LOCATION_ID', 'asia-northeast3') # e.g., 'asia-northeast3'
TASK_QUEUE_ID = os.environ.get('GCP_TASK_QUEUE_ID', 'my-batch-queue') # e.g., 'my-batch-queue'

# Pydantic 모델을 사용하여 요청 본문 유효성 검사
class BatchParams(BaseModel):
    batch_type: str
    params: dict = Field(default_factory=dict)

@app.get("/")
async def health_check():
    return "Tech News Summary (FASTAPI) Good!!"

@app.post("/trigger-batch", status_code=202)
async def trigger_batch(payload: BatchParams, request: Request):
    """
    Cloud Scheduler로부터 호출될 트리거 엔드포인트.
    실제 작업을 수행하는 대신 Cloud Tasks에 작업을 생성합니다.
    """
    if not all([PROJECT_ID, LOCATION_ID, TASK_QUEUE_ID]):
        raise HTTPException(status_code=500, detail="Server configuration error for Cloud Tasks.")

    # 워커 서비스의 URL을 가져옵니다.
    # 일반적으로 트리거와 워커는 같은 Cloud Run 서비스에 있으므로 현재 요청의 URL을 사용합니다.
    logger.info(f"request.base_url => {request.base_url}")
    base_url = str(request.base_url).replace('http://', 'https://')
    worker_url = str(base_url) + "execute-test-batch"

    # Cloud Tasks 큐 경로 설정
    parent = tasks_client.queue_path(PROJECT_ID, LOCATION_ID, TASK_QUEUE_ID)

    # Cloud Tasks에 보낼 작업(Task) 생성
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": worker_url,
            "headers": {"Content-Type": "application/json"},
            # payload를 JSON 문자열로 인코딩하여 본문에 추가
            "body": payload.json().encode(),
            # OIDC 토큰을 사용하여 워커 서비스 호출 인증
            "oidc_token": {
                # 워커를 호출할 서비스 계정. 비워두면 기본 Compute Engine 서비스 계정 사용
                "service_account_email": "sa-pvt-pjt-cloudtasks@theta-window-364015.iam.gserviceaccount.com"
            },
        },
        # 작업 실행 데드라인 설정 (최대 15분)
        "dispatch_deadline": duration_pb2.Duration(seconds=15*60),
    }

    try:
        # 작업 생성 요청
        response = tasks_client.create_task(parent=parent, task=task)
        logger.info(f"Created task: {response.name}")
        return {"message": "Batch task successfully queued.", "task_name": response.name}
    except Exception as e:
        logger.error(f"Error creating task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create task: {e}")


@app.post("/execute-test-batch")
async def execute_test_batch(payload: BatchParams):
    """
    Cloud Tasks에 의해 호출될 워커 엔드포인트.
    실제 오래 걸리는 배치 작업을 수행합니다.
    """
    batch_type = payload.batch_type
    params = payload.params
    logger.info(f"Worker received task for batch_type: {batch_type} with params: {params}")

    try:
        if batch_type == 'daily_report':
            run_daily_report(params)
        elif batch_type == 'user_sync':
            run_user_sync(params)
        else:
            logger.info(f"Error: Unknown batch type '{batch_type}'.")
            # Cloud Tasks가 재시도하지 않도록 성공(2xx) 응답을 반환하는 것이 중요
            return Response(status_code=204)

        logger.info(f"Successfully finished batch: {batch_type}")
        return Response(status_code=200) # 성공적으로 완료되면 200 OK
    except Exception as e:
        # 예외 발생 시, Cloud Tasks가 재시도하도록 에러(5xx) 응답을 반환
        logger.info(f"Error during batch execution: {e}", exc_info=True)
        raise HTTPException(status_code=503, detail=f"Batch job failed: {e}")


def run_daily_report(params: dict):
    """(오래 걸리는) 일일 리포트 생성 배치 작업 시뮬레이션"""
    
    logger.info(f"Running daily report... Params: {params}")
    time.sleep(300) # 5분 이상 소요되는 작업 시뮬레이션
    logger.info("Daily report generation finished.")

def run_user_sync(params: dict):
    """(오래 걸리는) 사용자 동기화 배치 작업 시뮬레이션"""
    logger.info(f"Running user synchronization... Params: {params}")
    time.sleep(400) # 5분 이상 소요되는 작업 시뮬레이션
    logger.info("User synchronization finished.")

    
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
        elif batch_type == 'tweet_rerun':
            base_ymd = params.get('base_ymd', None)
            backgroundtasks.add_task(run_tweet_rerun_batch, base_ymd)
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
    
    gcs_upload_json.main('zdnet', base_ymd)
    gcs_upload_json.main('thelec', base_ymd)
    
    news_summarizer.main(base_ymd)
    
    pwd = os.environ.get('NVR_MAIL_PWD')
    send_mail.main(pwd)
    
def run_tweet_batch():
    base_ymd = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")  # 기본값은 현재 날짜 (UTC 기준)
    
    tweet_scrapper_post.main(base_ymd)
    gcs_upload_json.main_tweet(base_ymd)
    tweet_summarizer.main(base_ymd)
    
    pwd = os.environ.get('NVR_MAIL_PWD')
    send_mail_tweet.main(pwd)
    
def run_tweet_rerun_batch(base_ymd=None):

    if not base_ymd:
        base_ymd = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")  # 기본값은 현재 날짜 (UTC 기준)

    gcs_download_json.download_gcs_posts_json_to_local(target_date=base_ymd)
    tweet_summarizer.main(base_ymd)

    pwd = os.environ.get('NVR_MAIL_PWD')
    send_mail_tweet.main(pwd)
    
    
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
