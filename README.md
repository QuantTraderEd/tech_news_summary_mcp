# 테크 뉴스 요약 MCP 서버

## 아키텍처 개요

- 클라이언트/트리거: 외부에서 HTTP 요청을 통해 Cloud Run 서비스의 FastAPI 엔드포인트를 호출합니다. (예: Postman, cURL, GCP Cloud Scheduler 등)
- GCP Cloud Run: Docker 이미지를 기반으로 FastAPI 애플리케이션을 호스팅합니다. 요청이 있을 때만 컨테이너가 실행되며, 트래픽에 따라 자동으로 확장됩니다.
- FastAPI 애플리케이션:
    - 데이터 수집 모듈:
      - 뉴스 웹사이트 크롤러: 지정된 테크 뉴스 웹사이트(ZDNet Korea, 디일렉, ETNEWS)에서 기사 제목, URL, 내용을 크롤링
      - Tweet 스크래퍼: Selenium(headless Chrome)으로 X(트위터)에 쿠키 로그인한 뒤 지정한 사용자들의 최신 게시글을 수집
    - 데이터 통합 및 정제 모듈: 수집된 뉴스 기사 및 트윗 데이터를 일관된 형식으로 통합하고, 불필요한 정보 제거, 중복 확인 등의 정제 작업을 수행합니다.
    - 요약 모듈: 크롤링된 기사 내용을 요약합니다. 이 기능은 Gemini API를 호출하여 AI 기반 번역 요약을 수행
    - 이메일 발송 모듈: 요약된 뉴스를 **SMTP(Simple Mail Transfer Protocol)** 를 통해 지정된 수신자에게 이메일로 발송

## 트위터 게시글 수집 (Selenium 스크래핑)
- **수집 대상**: `src/services/tweet_scrapper_post.py` 의 `TARGET_USERNAMES`, `TARGET_USERNAMES_2ND`
- **번역/요약**: 수집한 게시글을 Gemini API로 번역·요약하고, 일자별 누적 파일(`summarized_posts_agg.json`)과 비교해 중복 게시글에 `[중복]` 태그 처리

## 배치 실행 (POST /execute-batch)
요청 본문: `{"batch_type": "<type>", "params": {...}}` — 배치는 응답 반환 후 백그라운드로 실행됩니다.

| batch_type | params | 내용 |
|---|---|---|
| `news` | - | 뉴스 수집(ZDNet/디일렉/ETNEWS) → GCS 업로드 → 요약 → 메일 발송 |
| `tweet` | - | `TARGET_USERNAMES` 게시글 수집 → 번역/요약 |
| `tweet_2nd` | - | `TARGET_USERNAMES_2ND` 게시글 수집 → 번역/요약 |
| `tweet_rerun` | `base_ymd` | GCS 에 저장된 해당 일자 posts.json 으로 번역/요약만 재실행 |
| `tweet_single_user` | `base_ymd`, `tweet_username` | 한 사용자의 게시글만 수집 |
| `tweet_daily_dup` | `base_ymd`, `mode`(`default`/`mix`) | 일자별 중복 정리 후 Tweet 요약 메일 발송 |

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
│   ├── run_all_tweet_batch.sh     # tweets 스크래핑+요약+메일링 배치 스크립트
│   ├── send_summary.sh            # news 요약 메일 발송 스크립트
│   └── start_batch.sh             # news 수집+GCS 업로드+요약 배치 스크립트
├── src
│   ├── main.py                    # fastapi 서버 메인 코드
│   └── services
│       ├── gcs_download_json.py       # GCS -> 로컬 다운로드
│       ├── gcs_upload_json.py         # 로컬 -> GCS 업로드
│       ├── news_crawler_etnews.py     # ETNEWS 뉴스 크롤러
│       ├── news_crawler_thelec.py     # 디일렉 뉴스 크롤러
│       ├── news_crawler_zdnet.py      # ZDNet Korea 뉴스 크롤러
│       ├── news_summarizer.py         # 뉴스 요약 (Gemini)
│       ├── send_mail.py               # 뉴스 요약 메일 발송
│       ├── send_mail_tweet.py         # Tweet 요약 메일 발송
│       ├── tweet_daily_dup_check.py   # 일자별 Tweet 중복 체크/정리
│       ├── tweet_scrap_one_post.py    # (수동 실행용) URL 목록으로 개별 게시글 수집
│       ├── tweet_scrapper_post.py     # Tweet 스크래퍼 (Selenium)
│       └── tweet_summarizer.py        # Tweet 번역/요약 (Gemini)
├── tests                          # 단위테스트 
└── requirements.txt    
```

