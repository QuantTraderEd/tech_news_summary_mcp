import os
import site
import logging
import pytest

from unittest.mock import patch

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)
site.addsitedir(pjt_home_path)

# src.main 은 import 시점에 tweet_summarizer 를 불러오고, tweet_summarizer 는 config.json 이 없으면 sys.exit(1) 한다.
# (tests/test_tweet_summarizer.py 와 같은 방식으로 config.json 이 없으면 모듈 전체를 건너뛴다)
if not os.path.exists(os.path.join(pjt_home_path, 'config.json')):
    pytest.skip("config.json 이 없어 src.main 을 import 할 수 없습니다.", allow_module_level=True)

from src import main as app_main

# GCS 접근과 실제 수집·요약은 모두 mock 처리한다.


@pytest.mark.parametrize("ret, expected", [(0, True), (1, False), (2, False)])
def test_download_tweet_cookies_returns_success_flag(ret, expected):
    """download_gcs_to_local() 반환값이 0 일 때만 성공으로 본다 (1: GCS 에 파일 없음, 2: 버킷 접근 오류)."""
    with patch("src.main.gcs_download_json.download_gcs_to_local", return_value=ret) as mock_download:
        assert app_main.download_tweet_cookies() is expected

    mock_download.assert_called_once_with(file_name='tweet_cookies.json',
                                          bucket_name='gcs-private-pjt-data',
                                          gcs_base_path='config',
                                          date_str='20000101',
                                          local_file_path=app_main.pjt_home_path)


def test_download_tweet_cookies_returns_false_on_exception(caplog):
    with patch("src.main.gcs_download_json.download_gcs_to_local", side_effect=Exception("download error")):
        assert app_main.download_tweet_cookies() is False

    assert any(r.levelno == logging.ERROR for r in caplog.records)


TWEET_BATCHES = [
    ("run_tweet_batch", ()),
    ("run_tweet_2nd_batch", ()),
    ("run_tweet_single_user_batch", ("20261115", "fake_user")),
]


@pytest.mark.parametrize("batch_name, args", TWEET_BATCHES)
def test_tweet_batch_stops_when_cookie_download_fails(caplog, batch_name, args):
    """쿠키 파일을 내려받지 못하면 [쿠키 파일 오류] 로그를 남기고 수집·요약을 실행하지 않는다."""
    with patch("src.main.download_tweet_cookies", return_value=False), \
         patch("src.main.tweet_scrapper_post.main") as mock_scrape_main, \
         patch("src.main.tweet_summarizer.main") as mock_summary_main, \
         patch("src.main.count_tweet_posts") as mock_count:
        getattr(app_main, batch_name)(*args)

    mock_scrape_main.assert_not_called()
    mock_summary_main.assert_not_called()
    mock_count.assert_not_called()
    assert any(r.levelno == logging.ERROR and "[쿠키 파일 오류]" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("batch_name, args", TWEET_BATCHES)
def test_tweet_batch_runs_when_cookie_download_succeeds(batch_name, args):
    with patch("src.main.download_tweet_cookies", return_value=True), \
         patch("src.main.tweet_scrapper_post.main") as mock_scrape_main, \
         patch("src.main.tweet_summarizer.main"), \
         patch("src.main.count_tweet_posts"):
        getattr(app_main, batch_name)(*args)

    mock_scrape_main.assert_called_once()


# ---------------------------------------------------------------------------
# run_tweet_rerun_batch
# ---------------------------------------------------------------------------
import json  # noqa: E402

RERUN_YMD = "20261115"
RERUN_PREV_YMD = "20261114"


def test_rerun_targets_both_username_lists_and_passes_only_downloaded_users():
    """
    TARGET_USERNAMES + TARGET_USERNAMES_2ND 를 대상으로 내려받고(두 목록에 모두 있는 사용자는 한 번만),
    실제로 내려받은 사용자만 요약과 건수 집계에 넘긴다.
    """
    with patch.object(app_main.tweet_scrapper_post, 'TARGET_USERNAMES', ['a', 'b', 'shared']), \
         patch.object(app_main.tweet_scrapper_post, 'TARGET_USERNAMES_2ND', ['shared', 'c', 'd']), \
         patch("src.main.gcs_download_json.download_gcs_posts_json_to_local", return_value=['a', 'shared', 'd']) as mock_download, \
         patch("src.main.tweet_summarizer.main") as mock_summary_main, \
         patch("src.main.count_tweet_posts") as mock_count:
        app_main.run_tweet_rerun_batch(RERUN_YMD)

    mock_download.assert_called_once_with(target_user_list=['a', 'b', 'shared', 'c', 'd'], target_date=RERUN_YMD)
    mock_summary_main.assert_called_once_with(RERUN_YMD, tweet_usernames=['a', 'shared', 'd'])
    mock_count.assert_called_once_with(tweet_usernames=['a', 'shared', 'd'])


def test_rerun_skips_summary_with_warning_when_nothing_downloaded(caplog):
    """GCS 에 수집 파일이 있는 사용자가 한 명도 없으면 WARNING 을 남기고 요약·집계를 호출하지 않는다."""
    with patch.object(app_main.tweet_scrapper_post, 'TARGET_USERNAMES', ['a']), \
         patch.object(app_main.tweet_scrapper_post, 'TARGET_USERNAMES_2ND', ['b']), \
         patch("src.main.gcs_download_json.download_gcs_posts_json_to_local", return_value=[]), \
         patch("src.main.tweet_summarizer.main") as mock_summary_main, \
         patch("src.main.count_tweet_posts") as mock_count:
        app_main.run_tweet_rerun_batch(RERUN_YMD)

    mock_summary_main.assert_not_called()
    mock_count.assert_not_called()
    assert any(r.levelno == logging.WARNING and RERUN_YMD in r.getMessage() for r in caplog.records)


class _RerunGcs:
    """(날짜, 파일명) -> JSON 문자열을 보관하는 메모리 기반 가짜 GCS. 다운로드하면 로컬 data 디렉토리에 쓴다."""

    def __init__(self, data_dir):
        self.data_dir = data_dir
        self.store = {}
        self.downloads = []

    def download(self, file_name, date_str=None, **kwargs):
        self.downloads.append((date_str, file_name))
        if (date_str, file_name) not in self.store:
            return 1
        (self.data_dir / file_name).write_text(self.store[(date_str, file_name)], encoding='utf-8')
        return 0

    def upload(self, local_file_path, date_str=None, **kwargs):
        with open(local_file_path, 'r', encoding='utf-8') as f:
            self.store[(date_str, os.path.basename(local_file_path))] = f.read()
        return 0


def _collected(user, tag, text):
    return json.dumps({"data": [{"url": f"https://x.com/{user}/status/{tag}", "id": tag, "text": text}]})


@pytest.fixture
def rerun_env(tmp_path, mocker):
    """
    run_tweet_rerun_batch 를 실제 tweet_summarizer.main 까지 돌리되, 파일은 tmp_path 에서만 읽고 쓰며
    GCS 와 Gemini 는 모두 가짜로 대체한다.
    대상 사용자: TARGET_USERNAMES = [t1, t2], TARGET_USERNAMES_2ND = [s1, s2]
    """
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    gcs = _RerunGcs(data_dir)
    gemini_calls = []

    def fake_gemini(prompt):
        gemini_calls.append(prompt)
        return f"번역{len(gemini_calls)}"

    mocker.patch.object(app_main, 'pjt_home_path', str(tmp_path))
    mocker.patch.object(app_main.tweet_summarizer, 'pjt_home_path', str(tmp_path))
    mocker.patch.object(app_main.tweet_scrapper_post, 'TARGET_USERNAMES', ['t1', 't2'])
    mocker.patch.object(app_main.tweet_scrapper_post, 'TARGET_USERNAMES_2ND', ['s1', 's2'])
    # download_gcs_to_local 은 기본 인자(local_file_path)가 정의 시점의 실제 data/ 경로로 고정되므로 통째로 대체한다.
    mocker.patch.object(app_main.gcs_download_json, 'download_gcs_to_local', side_effect=gcs.download)
    mocker.patch.object(app_main.gcs_upload_json, 'upload_local_file_to_gcs', side_effect=gcs.upload)
    mocker.patch.object(app_main.tweet_summarizer, 'call_gemini_api', side_effect=fake_gemini)
    return data_dir, gcs, gemini_calls


def test_rerun_end_to_end_summarizes_only_users_present_in_gcs_from_both_lists(rerun_env):
    """
    GCS 에 수집 파일이 있는 t1, s1 만 요약한다. GCS 에 없는 t2 는 로컬에 예전 t2_posts.json 이 남아 있어도 처리하지 않고,
    2ND 목록의 s1 도 대상에 들어간다. (s2 는 GCS 에도 로컬에도 없음)
    """
    data_dir, gcs, gemini_calls = rerun_env
    gcs.store[(RERUN_YMD, 't1_posts.json')] = _collected('t1', '1', "t1 post text over fifteen chars")
    gcs.store[(RERUN_YMD, 's1_posts.json')] = _collected('s1', '2', "s1 post text over fifteen chars")
    (data_dir / 't2_posts.json').write_text(_collected('t2', '3', "STALE t2 post over fifteen chars"), encoding='utf-8')

    app_main.run_tweet_rerun_batch(RERUN_YMD)

    summarized = json.loads(gcs.store[(RERUN_YMD, 'summarized_posts.json')])
    assert [p['id'] for p in summarized] == ['1', '2']
    assert len(gemini_calls) == 2
    assert not any("STALE" in prompt for prompt in gemini_calls)
    agg = json.loads(gcs.store[(RERUN_YMD, 'summarized_posts_agg.json')])
    assert [p['id'] for p in agg] == ['1', '2']
    assert (RERUN_YMD, 't2_posts.json') in [(d, f) for d, f in gcs.downloads], "GCS 에 있는지 확인은 해야 한다"


def test_rerun_second_run_reuses_saved_summaries_without_gemini_calls(rerun_env):
    """같은 일자를 다시 rerun 하면 이미 요약한 게시물은 Gemini 를 부르지 않고, 새로 올라온 사용자 게시물만 요약한다."""
    data_dir, gcs, gemini_calls = rerun_env
    gcs.store[(RERUN_YMD, 't1_posts.json')] = _collected('t1', '1', "t1 post text over fifteen chars")

    app_main.run_tweet_rerun_batch(RERUN_YMD)
    assert len(gemini_calls) == 1
    first_agg = json.loads(gcs.store[(RERUN_YMD, 'summarized_posts_agg.json')])

    gemini_calls.clear()
    app_main.run_tweet_rerun_batch(RERUN_YMD)
    assert gemini_calls == []
    assert json.loads(gcs.store[(RERUN_YMD, 'summarized_posts_agg.json')]) == first_agg

    # 이후 s1 의 수집 파일이 새로 올라오면 s1 만 호출한다
    gcs.store[(RERUN_YMD, 's1_posts.json')] = _collected('s1', '2', "s1 post text over fifteen chars")
    app_main.run_tweet_rerun_batch(RERUN_YMD)
    assert len(gemini_calls) == 1
    agg = json.loads(gcs.store[(RERUN_YMD, 'summarized_posts_agg.json')])
    assert sorted(p['id'] for p in agg) == ['1', '2']


def test_rerun_end_to_end_nothing_in_gcs_does_not_touch_stale_local_files(rerun_env, caplog):
    """GCS 에 아무 수집 파일도 없으면 로컬에 남은 예전 파일이 있어도 요약하지 않고 업로드도 하지 않는다."""
    data_dir, gcs, gemini_calls = rerun_env
    (data_dir / 't1_posts.json').write_text(_collected('t1', '1', "STALE t1 post over fifteen chars"), encoding='utf-8')

    app_main.run_tweet_rerun_batch(RERUN_YMD)

    assert gemini_calls == []
    assert gcs.store == {}
    assert any(r.levelno == logging.WARNING for r in caplog.records)
