import os
import sys
import site
import logging
import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
# from pydantic import BaseModel
# from typing import List
# from src.services.news_crawler import NewsCrawler
# from src.services.summarizer import NewsSummarizer
# from src.services.email_sender import EmailSender

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Tech News Summary Notifier",
    description="반도체 뉴스 크롤링, 요약 및 이메일 발송 API",
    version="1.0.0"
)

# 서비스 인스턴스 초기화
# news_crawler = NewsCrawler("https://www.etnews.com/news/industry/semiconductor") # 실제 URL로 변경
# news_summarizer = NewsSummarizer()
# email_sender = EmailSender()

@app.get("/")
async def health_check():
    return "Tech News Summary MCP (FASTAPI) Good!!"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
