import os
import sys
import logging
import json

import google.generativeai as genai
import pytest

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)


# config.json 파일에서 API 키 불러오기
with open(f'{pjt_home_path}/config.json', 'r') as config_file:
    config = json.load(config_file)
    api_key = config.get("GEMINI_API_KEY")

# Gemini API 키 설정
genai.configure(api_key=api_key)

# 모델 선택 (예: Gemini 1.5 Flash)
model = genai.GenerativeModel('gemini-1.5-flash')

@pytest.mark.skip('need API KEY')
def test_call_gemini_api():
    # 프롬프트 설정 및 요청
    response = model.generate_content("한국의 수도는 어디인가요?")

    # 결과 출력
    result = response.text
    assert hasattr(response, 'text')
    print(result)

@pytest.mark.skip('need API KEY')
def test_translate_call_gemini_api():
    original_text = "xAI $200bn valuation ? It can be bigger than Tesla at this rate."
    translation_prompt = (f"Translate the following English text into a single, most natural and modern Korean sentence. "
                          f"Do not provide any other options or labels.:\n\n---\n{original_text}\n---")
    response = model.generate_content(translation_prompt)

    # 결과 출력
    result = response.text
    assert hasattr(response, 'text')
    print(result)

if __name__ == "__main__":
    # test_call_gemini_api()
    test_translate_call_gemini_api()
