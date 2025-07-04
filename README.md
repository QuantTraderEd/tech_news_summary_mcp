# 테크 뉴스 요약 MCP 서버

## 아키텍처 개요

- 클라이언트/트리거: 외부에서 HTTP 요청을 통해 Cloud Run 서비스의 FastAPI 엔드포인트를 호출합니다. (예: Postman, cURL, GCP Cloud Scheduler 등)
- GCP Cloud Run: Docker 이미지를 기반으로 FastAPI 애플리케이션을 호스팅합니다. 요청이 있을 때만 컨테이너가 실행되며, 트래픽에 따라 자동으로 확장됩니다.
- FastAPI 애플리케이션:
    - 데이터 수집 모듈:
      - 뉴스 웹사이트 크롤러: 지정된 반도체 관련 뉴스 웹사이트(예: ETNEWS, ZDNet 등)에서 기사 제목, URL, 내용을 크롤링
      - Twitter API 클라이언트: 트위터 API를 사용하여 특정 사용자최신 트윗을 수집
    - 데이터 통합 및 정제 모듈: 수집된 뉴스 기사 및 트윗 데이터를 일관된 형식으로 통합하고, 불필요한 정보 제거, 중복 확인 등의 정제 작업을 수행합니다.
    - 요약 모듈: 크롤링된 기사 내용을 요약합니다. 이 기능은 Gemini API를 호출하여 AI 기반 번역 요약을 수행
    - 이메일 발송 모듈: 요약된 뉴스를 **SMTP(Simple Mail Transfer Protocol)** 를 통해 지정된 수신자에게 이메일로 발송

## 트위터 게시글 조회 (API)
- **API 엔드포인트 호출**: users/:id/tweets와 같은 엔드포인트를 사용하여 특정 사용자의 최신 트윗을 조회
- **인증 필수**: 모든 API 요청은 OAuth 2.0 또는 기타 인증 방식을 통해 인증 필요

## GCP Cloud Run Service + Cloud Scheduler 활용
- Cloud Run Service (with Cloud Scheduler trigger):
    - 목적: HTTP 요청 처리.
    - 종료 조건: 요청 처리가 완료되고 일정 유휴 시간(Idle Timeout)이 지나면 인스턴스가 0으로 스케일 다운
    - 장점: 웹 서비스 및 배치 작업 겸용 가능. 기존 서비스 활용 가능.
    - 단점: 배치 작업의 명확한 종료 시점을 파악하기 어려울 수 있고, 긴 백그라운드 작업의 경우 인스턴스가 종료될 위험 있음.

## 프로젝트 구조

```
tech_news_summary_mcp/
├── Dockerfile
├── README.md
├── cloudbuild.yml                 # Google Cloud Build 설정 파일
├── data                           # json 파일데이터
├── proto_type                     # 프로토타입 개발용 소스코드
├── scripts
│   ├── generate_config.sh         # config.json 파일 생성 스크립트
│   ├── gunicorn_start.sh          # fastapi 서버 실행 스크립트
│   ├── run_all_batch.sh           # news 수집+요약+메일링 일괄 배치 스크립트
│   ├── run_all_tweet_batch.sh     # tweets 스크래핑 배치 스크립트
│   ├── send_summary.sh            # news 요약 메일 발송 스크립트
│   └── start_batch.sh             # news 수집 배치 스크립트 
├── src
│   ├── config.py
│   ├── main.py                    # fastapi 서버 메인 코드
│   └── services
│       ├── gcs_upload_json.py
│       ├── news_crawler_thelec.py
│       ├── news_crawler_zdnet.py
│       ├── news_summarizer.py
│       ├── send_mail.py
│       ├── send_mail_tweet.py
│       ├── tweet_scrapper_post.py
│       ├── tweet_summarizer.py
│       └── twitter_collector.py
├── tests                          # 단위테스트 
└── requirements.txt    
```

