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
