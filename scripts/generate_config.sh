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
  echo "오류: ${VERIFICATION_INFO_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${VERIFICATION_INFO_VAR_NAME}=\"your_verification_info_value\""
  exit 1
fi

# RECEIVER_EMAIL_LIST 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!RECEIVER_EMAIL_LIST_VAR_NAME}" ]; then
  echo "오류: ${RECEIVER_EMAIL_LIST_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${RECEIVER_EMAIL_LIST_VAR_NAME}=\"alice@example.com,bob@example.com,charlie@example.com\""
  exit 1
fi

# 환경 변수 값을 가져옵니다.
API_KEY_VALUE="${!API_KEY_VAR_NAME}"
TWEET_USERNAME_VALUE=" ${!TWEET_USERNAME_VAR_NAME}"
TWEET_PASSWORD_VALUE="${!TWEET_PASSWORD_VAR_NAME}" 
VERIFICATION_INFO_VALUE="${!VERIFICATION_INFO_VAR_NAME}"
IFS=',' read -ra EMAIL_ARRAY <<< "$RECEIVER_EMAIL_LIST"

# JSON 형식으로 출력할 문자열 준비
EMAIL_JSON=$(printf '"%s",' "${EMAIL_ARRAY[@]}")
EMAIL_JSON="[${EMAIL_JSON%,}]"  # 마지막 쉼표 제거하고 대괄호로 감싸기

# config.json 파일을 생성합니다.
# 따옴표가 JSON 형식에 맞게 올바르게 처리되도록 이스케이프 처리합니다.
echo "{" > "${CONFIG_FILE}"
echo "  \"${API_KEY_VAR_NAME}\": \"${API_KEY_VALUE}\"," >> "${CONFIG_FILE}"
echo "  \"${TWEET_USERNAME_VAR_NAME}\": \"${TWEET_USERNAME_VALUE}\"," >> "${CONFIG_FILE}"
echo "  \"${TWEET_PASSWORD_VAR_NAME}\": \"${TWEET_PASSWORD_VALUE}\"," >> "${CONFIG_FILE}"
echo "  \"${VERIFICATION_INFO_VAR_NAME}\": \"${VERIFICATION_INFO_VALUE}\"," >> "${CONFIG_FILE}"
echo "  \"${RECEIVER_EMAIL_LIST_VAR_NAME}\": ${EMAIL_JSON}" >> "${CONFIG_FILE}"
echo "}" >> "${CONFIG_FILE}"

echo "${CONFIG_FILE} 파일이 성공적으로 생성되었습니다."
# echo "내용:"
# cat "${CONFIG_FILE}"


