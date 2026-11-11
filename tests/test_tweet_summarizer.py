import os
import site
import json

import pytest

# Add project root to the Python path
src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# tweet_summarizer 는 import 시점에 config.json 의 GEMINI_API_KEY 를 읽고, 없으면 sys.exit(1) 한다.
if not os.path.exists(os.path.join(pjt_home_path, 'config.json')):
    pytest.skip("config.json 이 없어 tweet_summarizer 를 import 할 수 없습니다.", allow_module_level=True)

from src.services import tweet_summarizer

BASE_YMD = "20250628"
PREV_YMD = "20250627"
URL_A = "https://x.com/user1/status/1"
URL_B = "https://x.com/user1/status/2"


@pytest.fixture
def summarizer_env(tmp_path, mocker):
    """
    tweet_summarizer.main 이 임시 디렉토리에서만 파일을 읽고 쓰도록 하고,
    Gemini / GCS 호출을 모두 목(mock) 처리한다.
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    mocker.patch.object(tweet_summarizer, 'pjt_home_path', str(tmp_path))
    mocker.patch.object(tweet_summarizer.gcs_upload_json, 'upload_local_file_to_gcs', return_value=0)

    def fake_process_posts(input_filename, summarized_posts):
        summarized_posts.append({"url": URL_A, "id": "1", "created_at": "2025-06-28T01:00:00.000Z",
                                 "text": "long post", "title": "제목 A", "summary": "* 요약"})
        summarized_posts.append({"url": URL_B, "id": "2", "created_at": "2025-06-28T02:00:00.000Z",
                                 "text": "short post", "translated_text": "번역 B"})

    mocker.patch.object(tweet_summarizer, 'process_posts', side_effect=fake_process_posts)
    return data_dir


def _load_summarized_posts(data_dir):
    with open(data_dir / "summarized_posts.json", 'r', encoding='utf-8') as f:
        return {post['url']: post for post in json.load(f)}


def test_no_dup_tag_when_prev_agg_missing(summarizer_env, mocker):
    """전일자 agg 파일이 GCS 에 없으면 어떤 게시물에도 [중복] 태그가 붙지 않아야 한다."""
    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', return_value=1)

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['user1'])

    posts = _load_summarized_posts(summarizer_env)
    assert posts[URL_A]['title'] == "제목 A"
    assert posts[URL_B]['translated_text'] == "번역 B"


def test_dup_tag_only_for_posts_in_prev_agg(summarizer_env, mocker):
    """전일자 agg 파일에 있는 url 의 게시물에만 [중복] 태그가 붙어야 한다."""
    agg_path = summarizer_env / "summarized_posts_agg.json"

    def fake_download(file_name, date_str=None, **kwargs):
        if date_str == PREV_YMD:
            with open(agg_path, 'w', encoding='utf-8') as f:
                json.dump([{"url": URL_A, "title": "어제 수집된 A"}], f, ensure_ascii=False)
            return 0
        return 1  # 당일자 agg 는 아직 없음

    mocker.patch.object(tweet_summarizer.gcs_download_json, 'download_gcs_to_local', side_effect=fake_download)

    tweet_summarizer.main(BASE_YMD, gcs_mode=True, tweet_usernames=['user1'])

    posts = _load_summarized_posts(summarizer_env)
    assert posts[URL_A]['title'] == "[중복] 제목 A"
    assert posts[URL_B]['translated_text'] == "번역 B"
