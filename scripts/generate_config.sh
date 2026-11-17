#!/bin/bash

# config.json 파일에 저장할 API-KEY 환경 변수의 이름을 정의합니다.
API_KEY_VAR_NAME="GEMINI_API_KEY"
TWEET_USERNAME_VAR_NAME="TWEET_USERNAME"
TWEET_PASSWORD_VAR_NAME="TWEET_PASSWORD"
VERIFICATION_INFO_VAR_NAME="VERIFICATION_INFO"
RECEIVER_EMAIL_LIST_VAR_NAME="RECEIVER_EMAIL_LIST"
# 생성될 config 파일의 이름을 정의합니다.
CONFIG_FILE="config.json"

# API-KEY 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!API_KEY_VAR_NAME}" ]; then
  echo "ERROR: ${API_KEY_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${API_KEY_VAR_NAME}=\"your_api_key_value\""
  exit 1
fi

# TWEET_USERNAME_VAR_NAME 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!TWEET_USERNAME_VAR_NAME}" ]; then
  echo "ERROR: ${TWEET_USERNAME_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${TWEET_USERNAME_VAR_NAME}=\"your_tweet_username_value\""
  exit 1
fi

# TWEET_PASSWORD_VAR_NAME 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!TWEET_PASSWORD_VAR_NAME}" ]; then
  echo "ERROR: ${TWEET_PASSWORD_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${TWEET_PASSWORD_VAR_NAME}=\"your_tweet_password_value\""
  exit 1
fi

# VERIFICATION-INFO 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!VERIFICATION_INFO_VAR_NAME}" ]; then
  echo "ERROR: ${VERIFICATION_INFO_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${VERIFICATION_INFO_VAR_NAME}=\"your_verification_info_value\""
  exit 1
fi

# RECEIVER_EMAIL_LIST 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!RECEIVER_EMAIL_LIST_VAR_NAME}" ]; then
  echo "ERROR: ${RECEIVER_EMAIL_LIST_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${RECEIVER_EMAIL_LIST_VAR_NAME}=\"alice@example.com,bob@example.com,charlie@example.com\""
  exit 1
fi

# 값을 JSON 문자열 리터럴(큰따옴표 포함)로 바꿉니다.
# 값은 stdin 으로 넘겨서 프로세스 인자 목록(ps)에 비밀 값이 나타나지 않게 합니다.
json_string() {
  printf '%s' "$1" | python3 -c 'import json, sys; sys.stdout.write(json.dumps(sys.stdin.read()))'
}

# 환경 변수 값을 가져옵니다.
API_KEY_VALUE="${!API_KEY_VAR_NAME}"
TWEET_USERNAME_VALUE="${!TWEET_USERNAME_VAR_NAME}"
TWEET_PASSWORD_VALUE="${!TWEET_PASSWORD_VAR_NAME}"
VERIFICATION_INFO_VALUE="${!VERIFICATION_INFO_VAR_NAME}"
IFS=',' read -ra EMAIL_ARRAY <<< "${!RECEIVER_EMAIL_LIST_VAR_NAME}"

# 모든 값을 JSON 문자열 리터럴로 먼저 변환합니다.
# 변환이 하나라도 실패하면 config.json 을 쓰기 전에 종료해서 기존 파일이 깨지지 않게 합니다.
# (오류 메시지에는 값이 아니라 변수 이름만 적습니다.)
API_KEY_JSON=$(json_string "${API_KEY_VALUE}") || {
  echo "ERROR: ${API_KEY_VAR_NAME} 값을 JSON 으로 변환하지 못했습니다. python3 가 설치되어 있는지 확인하세요."
  exit 1
}
TWEET_USERNAME_JSON=$(json_string "${TWEET_USERNAME_VALUE}") || {
  echo "ERROR: ${TWEET_USERNAME_VAR_NAME} 값을 JSON 으로 변환하지 못했습니다. python3 가 설치되어 있는지 확인하세요."
  exit 1
}
TWEET_PASSWORD_JSON=$(json_string "${TWEET_PASSWORD_VALUE}") || {
  echo "ERROR: ${TWEET_PASSWORD_VAR_NAME} 값을 JSON 으로 변환하지 못했습니다. python3 가 설치되어 있는지 확인하세요."
  exit 1
}
VERIFICATION_INFO_JSON=$(json_string "${VERIFICATION_INFO_VALUE}") || {
  echo "ERROR: ${VERIFICATION_INFO_VAR_NAME} 값을 JSON 으로 변환하지 못했습니다. python3 가 설치되어 있는지 확인하세요."
  exit 1
}

# 이메일 목록은 각 원소를 JSON 문자열로 바꿔 ["a","b"] 형태로 조립합니다.
EMAIL_JSON=""
for EMAIL_ITEM in "${EMAIL_ARRAY[@]}"; do
  EMAIL_ITEM_JSON=$(json_string "${EMAIL_ITEM}") || {
    echo "ERROR: ${RECEIVER_EMAIL_LIST_VAR_NAME} 값을 JSON 으로 변환하지 못했습니다. python3 가 설치되어 있는지 확인하세요."
    exit 1
  }
  EMAIL_JSON="${EMAIL_JSON}${EMAIL_ITEM_JSON},"
done
EMAIL_JSON="[${EMAIL_JSON%,}]"  # 마지막 쉼표 제거하고 대괄호로 감싸기

# config.json 파일을 생성합니다.
# 값은 위에서 json.dumps 로 이스케이프한 JSON 리터럴이므로 그대로 기록합니다.
# 값에 포함된 \ 가 해석되지 않도록 echo 대신 printf '%s\n' 을 사용합니다.
{
  printf '%s\n' "{"
  printf '%s\n' "  \"${API_KEY_VAR_NAME}\": ${API_KEY_JSON},"
  printf '%s\n' "  \"${TWEET_USERNAME_VAR_NAME}\": ${TWEET_USERNAME_JSON},"
  printf '%s\n' "  \"${TWEET_PASSWORD_VAR_NAME}\": ${TWEET_PASSWORD_JSON},"
  printf '%s\n' "  \"${VERIFICATION_INFO_VAR_NAME}\": ${VERIFICATION_INFO_JSON},"
  printf '%s\n' "  \"${RECEIVER_EMAIL_LIST_VAR_NAME}\": ${EMAIL_JSON}"
  printf '%s\n' "}"
} > "${CONFIG_FILE}"

echo "${CONFIG_FILE} 파일이 성공적으로 생성되었습니다."
# echo "내용:"
# cat "${CONFIG_FILE}"


