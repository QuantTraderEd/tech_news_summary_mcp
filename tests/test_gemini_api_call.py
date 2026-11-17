import os
import json

import google.generativeai as genai
# 새 SDK(google-genai). 구 SDK 의 genai 이름과 겹치므로 별칭으로 import
from google import genai as google_genai
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

# 모델 선택 (예: Gemini 3.5 Flash Lite)
# https://cloud.google.com/vertex-ai/generative-ai/docs/learn/model-versions
model = genai.GenerativeModel('gemini-3.5-flash-lite')

# 새 SDK(google-genai) 클라이언트 생성 (위에서 읽은 api_key 재사용)
google_genai_client = google_genai.Client(api_key=api_key)

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


# ---------------------------------------------------------------------------
# 새 SDK(google-genai) 버전: 위의 구 SDK(google-generativeai) 테스트와 같은 일을 한다.
# 호출 방식만 model.generate_content(프롬프트) -> client.models.generate_content(model=..., contents=...) 로 다르다.
# ---------------------------------------------------------------------------

@pytest.mark.skip('need API KEY')
def test_call_gemini_api_google_genai():
    # 프롬프트 설정 및 요청
    response = google_genai_client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents="한국의 수도는 어디인가요?")

    # 결과 출력
    result = response.text
    assert hasattr(response, 'text')
    print(result)

@pytest.mark.skip('need API KEY')
def test_translate_call_gemini_api_google_genai():
    original_text = "xAI $200bn valuation ? It can be bigger than Tesla at this rate."
    translation_prompt = (f"Translate the following English text into a single, most natural and modern Korean sentence. "
                          f"Do not provide any other options or labels.:\n\n---\n{original_text}\n---")
    response = google_genai_client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=translation_prompt)

    # 결과 출력
    result = response.text
    assert hasattr(response, 'text')
    print(result)

@pytest.mark.skip('need API KEY')
def test_summary_call_gemini_api_google_genai():
    original_text = "$NVDA China restart faces production obstacles reported by Reuters, citing The Information The U.S. government's April ban on sales of the H20 chips had forced Nvidia to void customer orders and cancel manufacturing capacity it had booked at $TSM, said the report in tech publication The Information, citing two people with knowledge of the matter. TSMC had shifted its H20 production lines to produce other chips for other customers, and manufacturing new chips from scratch could take nine months, Nvidia CEO Jensen Huang said at a media event in Beijing this week, according to the report. The report also said Nvidia did not plan to restart production, without citing any sources or giving details. *any surprise here?"
    summary_prompt = (
        f"Summarize the following English text into a 3-point bullet list in most natural and modern Korean. "
        f"Do not provide any other explanations or the original English text.:\n\n"
        f"---\n{original_text}\n---")

    response = google_genai_client.models.generate_content(
        model='gemini-3.5-flash-lite',
        contents=summary_prompt)

    # 결과 출력
    result = response.text
    assert hasattr(response, 'text')
    print(result)


if __name__ == "__main__":
    print("=== 구 SDK (google-generativeai) ===")
    # test_call_gemini_api()
    # test_translate_call_gemini_api()
    test_summary_call_gemini_api()

    print("=== 새 SDK (google-genai) ===")
    # test_call_gemini_api_google_genai()
    # test_translate_call_gemini_api_google_genai()
    test_summary_call_gemini_api_google_genai()
