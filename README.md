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
├── Dockerfile
├── requirements.txt
└── .env
```

### 주요 파일별 역할 (세부 소스코드 제외)

- app/main.py: FastAPI 애플리케이션의 핵심 로직을 포함합니다. HTTP 엔드포인트를 정의하고, 각 서비스 모듈을 호출하여 뉴스 수집, 요약, 이메일 발송 과정을 조율합니다.  
- app/config.py: 애플리케이션에서 사용하는 모든 환경 변수(API 키, SMTP 정보 등)를 로드하고 관리합니다.  
- app/services/: 실제 비즈니스 로직을 구현하는 모듈들을 담는 디렉토리입니다.  
    - news_crawler.py: 특정 웹사이트에서 반도체 관련 뉴스 기사를 크롤링하는 기능을 담당합니다.  
    - twitter_collector.py: X(트위터) API를 사용하여 특정 사용자나 키워드에 해당하는 트윗 데이터를 수집하는 기능을 담당합니다. (X API 연동 로직 포함)  
    - data_integrator.py: news_crawler와 twitter_collector에서 수집된 다양한 형태의 데이터를 표준화된 NewsItem 모델로 변환하고, 중복을 제거하며, 보고서 작성을 위해 데이터를 준비합니다.  
    - summarizer.py: data_integrator에서 전달받은 텍스트 데이터를 요약하는 기능을 담당합니다. LLM API를 호출하거나 자체 요약 로직을 포함할 수 있습니다.  
    - email_sender.py: 요약된 보고서를 이메일로 구성하고 지정된 수신자에게 발송하는 기능을 담당합니다.  
- app/models/news_item.py: 크롤링된 뉴스 기사와 트윗 데이터를 통합하여 관리하기 위한 데이터 구조(예: 제목, 내용, URL, 원본 소스, 날짜 등)를 정의합니다.  
- Dockerfile: 애플리케이션을 실행하기 위한 Docker 이미지를 빌드하는 방법을 정의합니다. 파이썬 버전, 의존성 설치, 애플리케이션 코드 복사 및 실행 명령어 등을 포함합니다.  
- requirements.txt: 프로젝트에 필요한 모든 Python 라이브러리 목록을 정의합니다. tweepy와 같이 X API를 다루기 위한 라이브러리가 추가될 것입니다.  
- .env: 로컬 개발 환경에서 사용될 환경 변수들을 저장합니다. 배포 시에는 GCP Secret Manager와 같은 보안 서비스를 통해 관리될 것입니다.  



