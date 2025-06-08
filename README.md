# 테크 뉴스 요약 MCP 서버

## 아키텍처 개요

- 클라이언트/트리거: 외부에서 HTTP 요청을 통해 Cloud Run 서비스의 FastAPI 엔드포인트를 호출합니다. (예: Postman, cURL, GCP Cloud Scheduler 등)
- GCP Cloud Run: Docker 이미지를 기반으로 FastAPI 애플리케이션을 호스팅합니다. 요청이 있을 때만 컨테이너가 실행되며, 트래픽에 따라 자동으로 확장됩니다.
- FastAPI 애플리케이션:

    - 데이터 수집 모듈 (확장):
      - 뉴스 웹사이트 크롤러: 지정된 반도체 관련 뉴스 웹사이트(예: ETNEWS, ZDNet Korea 등)에서 기사 제목, URL, 내용을 크롤링합니다.
      - Twitter API 클라이언트: X(트위터) API를 사용하여 특정 사용자(예: @dnystedt) 또는 키워드 관련 최신 트윗을 수집합니다. 이 부분은 X API 정책 및 접근 권한에 따라 구현이 달라질 수 있습니다.
    - 데이터 통합 및 정제 모듈: 수집된 뉴스 기사 및 트윗 데이터를 일관된 형식으로 통합하고, 불필요한 정보 제거, 중복 확인 등의 정제 작업을 수행합니다.
    - 요약 모듈: 크롤링된 기사 내용을 요약합니다. 이 기능은 외부 LLM(Large Language Model) API(예: Google Gemini API, OpenAI GPT)를 호출하여 AI 기반 요약을 수행하거나, 간단한 규칙 기반 요약을 사용할 수 있습니다.
    - 이메일 발송 모듈: 요약된 뉴스를 **SMTP(Simple Mail Transfer Protocol)** 를 통해 지정된 수신자에게 이메일로 발송합니다. SendGrid, Mailgun, AWS SES, Gmail SMTP 등 다양한 이메일 서비스 공급자를 활용할 수 있습니다.

**외부 뉴스 사이트** : 크롤링 대상이 되는 반도체 뉴스 웹사이트입니다.
**X (Twitter) API 서비스** : 트윗 데이터를 가져오기 위한 X의 공식 API입니다. 유료 구독 및 API 키가 필요할 수 있습니다.
**LLM API 서비스** : 뉴스 요약을 위한 AI 모델을 제공하는 서비스입니다 (예: Google Gemini API, OpenAI API).
**SMTP 서버/이메일 서비스** : 이메일 발송을 위한 서버 또는 클라우드 기반 이메일 서비스입니다.

## 트위터 게시글 조회 (API) 검토
- **API 엔드포인트 호출**: users/:id/tweets와 같은 엔드포인트를 사용하여 특정 사용자의 최신 트윗을 가져옵니다.
- **제한 및 요금**: X API는 사용량에 따라 다양한 접근 계층(Free, Basic, Pro, Enterprise)을 제공합니다. 무료 계층은 매우 제한적인 기능만 제공하며, 주기적으로 특정 사용자 게시글을 대량으로 크롤링하려면 유료 구독이 필요할 수 있습니다. 예를 들어, 특정 사용자(예: @dnystedt)의 타임라인을 가져오는 기능은 일반적으로 GET /2/users/:id/tweets 엔드포인트를 통해 가능하지만, 이 역시 접근 수준에 따라 제한이 따릅니다  
- **인증 필수**: 모든 API 요청은 OAuth 2.0 또는 기타 인증 방식을 통해 인증되어야 합니다.

## Cloud Run 작동 방식 검토
핵심 차이점 (Cloud Run Job vs. Cloud Run Service with Scheduler)


- Cloud Run Job:
    - 목적: 단일 작업 실행 후 종료.
    - 종료 조건: 컨테이너의 메인 프로세스가 종료되면 작업 완료로 간주하고 인스턴스가 종료됨.
    - 최대 실행 시간: 최대 24시간.
    - 비용: 작업 실행 시간에 따라 정확히 과금.
    - 장점: 배치 작업에 최적화. 병렬 처리, 재시도 로직 내장. 작업 완료 시점 명확.
    - 단점: HTTP 요청을 직접 받기보다는 Cloud Scheduler의 트리거를 통해 "실행"됨.


- Cloud Run Service (with Cloud Scheduler trigger):
    - 목적: HTTP 요청 처리.
    - 종료 조건: 요청 처리가 완료되고 일정 유휴 시간(Idle Timeout)이 지나면 인스턴스가 0으로 스케일 다운될 수 있음. 백그라운드 태스크가 실행 중이라도 HTTP 응답이 나간 후 유휴 시간이 지나면 종료될 위험 있음.
    - 최대 요청 처리 시간: HTTP 요청에 대한 응답은 최대 60분 (기본 5분, 설정 가능). 그러나 실제 백그라운드 작업은 이 시간 이후에도 실행될 수 있지만, 인스턴스 종료 위험이 커짐.
    - 비용: 요청 처리 시간 및 인스턴스 활성 시간 동안 과금. 유휴 시간도 과금될 수 있음.
    - 장점: 웹 서비스 및 배치 작업 겸용 가능. 기존 서비스 활용 가능.
    - 단점: 배치 작업의 명확한 종료 시점을 파악하기 어려울 수 있고, 긴 백그라운드 작업의 경우 인스턴스가 종료될 위험이 있어 min-instances 설정이 필요할 수 있음. 배치 작업에 대한 재시도 로직은 직접 구현해야 함.

- 언제 Cloud Run Service를 사용하고 언제 Cloud Run Job을 사용하는가?

    - Cloud Run Job: 순수하게 "특정 작업을 실행하고 완료되면 끝나는" 배치 작업에 강력히 권장됩니다. 작업의 시작과 끝이 명확하며, 재시도 메커니즘이 내장되어 있어 안정적입니다.
    - Cloud Run Service + Cloud Scheduler:
        - 단기 배치 작업: 백그라운드에서 빠르게 완료되는 작업이거나, 이미 존재하는 웹 서비스 내에서 트리거되는 간단한 작업에 적합합니다.
        - 서비스와 배치 기능 결합: 웹 서비스가 특정 요청을 받아 내부적으로 비동기 배치 작업을 시작해야 할 때 유용합니다.
        - Cloud Run Job이 필요 없을 정도의 간단한 스케줄링: 예를 들어, 매일 특정 시간마다 캐시를 갱신하는 등의 가벼운 작업.

결론적으로, Cloud Run Service에서 Cloud Scheduler를 통한 POST 호출은 FastAPI가 백그라운드 태스크를 시작하고 즉시 응답을 반환하여 Cloud Scheduler에게 작업 시작을 알리는 방식으로 작동합니다. 이후 실제 배치 작업은 Cloud Run 인스턴스 내에서 비동기적으로 실행됩니다. 장기 실행 배치 작업이라면 Cloud Run Job을 사용하는 것이 더 견고하고 예측 가능한 결과를 얻을 수 있습니다.

## 프로젝트 구조

```
tech_news_summary_mcp/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── news_crawler.py      # 웹사이트 크롤링
│   │   ├── twitter_collector.py # Twitter API 연동 및 데이터 수집 (신규)
│   │   ├── data_integrator.py   # 수집된 데이터 통합 및 정제 (신규)
│   │   ├── summarizer.py
│   │   └── email_sender.py
│   └── models/
│       ├── __init__.py
│       └── news_item.py         # 뉴스 및 트윗 데이터를 통합하여 표현할 데이터 모델 (신규)
├── data/                        # json 파일 데이터
├── tests                        # 단위 테스트 코드
├── Dockerfile
├── requirements.txt
└── .env
```

### 주요 파일과 폴더별 역할

- app/main.py: FastAPI 애플리케이션의 핵심 로직을 포함합니다. HTTP 엔드포인트를 정의하고, 각 서비스 모듈을 호출하여 뉴스 수집, 요약, 이메일 발송 과정을 조율합니다.  
- app/config.py: 애플리케이션에서 사용하는 모든 환경 변수(API 키, SMTP 정보 등)를 로드하고 관리합니다.  
- app/services/: 실제 비즈니스 로직을 구현하는 모듈들을 담는 디렉토리입니다.  
    - news_crawler.py: 특정 웹사이트에서 반도체 관련 뉴스 기사를 크롤링하는 기능을 담당합니다.  
    - twitter_collector.py: X(트위터) API를 사용하여 특정 사용자나 키워드에 해당하는 트윗 데이터를 수집하는 기능을 담당합니다. (X API 연동 로직 포함)  
    - data_integrator.py: news_crawler와 twitter_collector에서 수집된 다양한 형태의 데이터를 표준화된 NewsItem 모델로 변환하고, 중복을 제거하며, 보고서 작성을 위해 데이터를 준비합니다.  
    - summarizer.py: data_integrator에서 전달받은 텍스트 데이터를 요약하는 기능을 담당합니다. LLM API를 호출하거나 자체 요약 로직을 포함할 수 있습니다.  
    - email_sender.py: 요약된 보고서를 이메일로 구성하고 지정된 수신자에게 발송하는 기능을 담당합니다.  
- app/models/news_item.py: 크롤링된 뉴스 기사와 트윗 데이터를 통합하여 관리하기 위한 데이터 구조(예: 제목, 내용, URL, 원본 소스, 날짜 등)를 정의합니다.
- data/ 뉴스데이터 수집 후 json 파일로 저장할 위치. json 파일은 GCS 에 일자별 저장
- Dockerfile: 애플리케이션을 실행하기 위한 Docker 이미지를 빌드하는 방법을 정의합니다. 파이썬 버전, 의존성 설치, 애플리케이션 코드 복사 및 실행 명령어 등을 포함합니다.  
- requirements.txt: 프로젝트에 필요한 모든 Python 라이브러리 목록을 정의합니다. tweepy와 같이 X API를 다루기 위한 라이브러리가 추가될 것입니다.  
- .env: 로컬 개발 환경에서 사용될 환경 변수들을 저장합니다. 배포 시에는 GCP Secret Manager와 같은 보안 서비스를 통해 관리될 것입니다.  



