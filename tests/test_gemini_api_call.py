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
# https://cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions
model = genai.GenerativeModel('gemini-2.0-flash-lite')

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

@pytest.mark.skip('need API KEY')
def test_summary_call_gemini_api():
    original_text = "$NVDA China restart faces production obstacles reported by Reuters, citing The Information The U.S. government's April ban on sales of the H20 chips had forced Nvidia to void customer orders and cancel manufacturing capacity it had booked at $TSM, said the report in tech publication The Information, citing two people with knowledge of the matter. TSMC had shifted its H20 production lines to produce other chips for other customers, and manufacturing new chips from scratch could take nine months, Nvidia CEO Jensen Huang said at a media event in Beijing this week, according to the report. The report also said Nvidia did not plan to restart production, without citing any sources or giving details. *any surprise here?"
    summary_prompt = (
        f"Summarize the following English text into a 3-point bullet list in most natural and modern Korean. "
        f"Do not provide any other explanations or the original English text.:\n\n"
        f"---\n{original_text}\n---")

    response = model.generate_content(summary_prompt)

    # 결과 출력
    result = response.text
    assert hasattr(response, 'text')
    print(result)


if __name__ == "__main__":
    # test_call_gemini_api()
    # test_translate_call_gemini_api()
    test_summary_call_gemini_api()
