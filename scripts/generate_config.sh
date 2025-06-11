#!/bin/bash

# config.json 파일에 저장할 API-KEY 환경 변수의 이름을 정의합니다.
API_KEY_VAR_NAME="GEMINI_API_KEY"
# 생성될 config 파일의 이름을 정의합니다.
CONFIG_FILE="config.json"

# API-KEY 환경 변수가 설정되어 있는지 확인합니다.
if [ -z "${!API_KEY_VAR_NAME}" ]; then
  echo "ERROR: ${API_KEY_VAR_NAME} 환경 변수가 설정되지 않았습니다."
  echo "예시: export ${API_KEY_VAR_NAME}=\"your_api_key_value\""
  # exit 1
fi

# 환경 변수 값을 가져옵니다.
API_KEY_VALUE="${!API_KEY_VAR_NAME}"

# config.json 파일을 생성합니다.
# 따옴표가 JSON 형식에 맞게 올바르게 처리되도록 이스케이프 처리합니다.
echo -e "{\n  \"${API_KEY_VAR_NAME}\": \"${API_KEY_VALUE}\"\n}" > "${CONFIG_FILE}"

echo "${CONFIG_FILE} 파일이 성공적으로 생성되었습니다."
# echo "내용:"
# cat "${CONFIG_FILE}"


