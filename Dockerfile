# 기본 이미지로 python:3.10-slim을 사용합니다.
FROM python:3.10-slim

# 작업 디렉토리를 /app으로 설정합니다.
WORKDIR /app

# 시스템 패키지 업데이트 및 크롬 의존성 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    ca-certificates \
    # 크롬 브라우저 실행에 필요한 라이브러리들
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    libdbus-1-3 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libgbm-dev \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 구글 크롬 브라우저 설치
# wget으로 구글의 공식 배포판을 다운로드하여 설치
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 현재 디렉토리의 모든 파일과 폴더를 /app 디렉토리로 복사합니다.
# 이 작업은 root 권한으로 수행되어 모든 파일이 컨테이너 내부에 존재하도록 합니다.
COPY . .

# 'app' 그룹과 'app' 사용자를 생성하고, 쉘을 /bin/bash로 설정합니다.
# --system 플래그는 시스템 사용자를 생성합니다 (UID/GID가 낮은 번호).
# -m 플래그는 사용자의 홈 디렉토리를 생성합니다.
RUN groupadd --system app && useradd --system -g app -s /bin/bash -m app

# /app 디렉토리 하위의 모든 파일과 폴더의 소유자와 그룹을 'app' 계정으로 변경합니다.
# 이 명령은 'app' 사용자로 전환하기 전에 root 권한으로 실행되어야 합니다.
RUN chown -R app:app /app

# start_batch.sh 스크립트에 실행 권한을 부여합니다.
# 이 명령은 아직 root 사용자로 실행되므로 권한 문제가 없습니다.
RUN chmod +x ./scripts/start_batch.sh
RUN chmod +x ./scripts/gunicorn_start.sh

# 'app' 사용자로 전환합니다. 이 이후의 모든 명령은 'app' 사용자로 실행됩니다.
USER app

# app 사용자의 .local/bin 디렉토리를 PATH 환경변수에 추가합니다.
# pip가 스크립트를 설치할 때 이 경로를 사용하며,
# 이 스크립트가 PATH에 포함되어 쉽게 실행될 수 있도록 합니다.
ENV PATH="/home/app/.local/bin:${PATH}"

# requirements.txt 파일에 명시된 모든 Python 패키지를 설치합니다.
# 이 명령은 'app' 사용자로 실행됩니다.
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8080

# 컨테이너가 시작될 때 run_all_batch.sh 스크립트를 실행합니다.
# exec 형식은 시그널 처리를 올바르게 합니다.
# CMD ["/app/scripts/run_all_batch.sh"]
CMD ["/app/scripts/gunicorn_start.sh"]
