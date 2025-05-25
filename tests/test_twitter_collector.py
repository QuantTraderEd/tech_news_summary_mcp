import os
import sys
import site
import logging
import unittest

from unittest.mock import MagicMock, patch

import tweepy # tweepy.TweepyException을 모의하기 위해 필요

# 테스트 실행 전에 X_BEARER_TOKEN 환경 변수를 설정합니다.
# 실제 토큰이 아니어도 되며, 값이 존재하기만 하면 됩니다.
os.environ["X_BEARER_TOKEN"] = "test_bearer_token_for_mocking"

src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

from app.services.twitter_collector import TwitterCollector

class TestTwitterCollector(unittest.TestCase):
    """
    TwitterCollector 클래스의 단위 테스트를 수행합니다.
    실제 X API 호출을 방지하기 위해 tweepy.Client를 모의(mock)합니다.
    """

    def setUp(self):
        # 테스트 시작 전 TwitterCollector 인스턴스를 생성합니다.
        # tweepy.Client 초기화 시 실제 네트워크 호출을 하지 않도록 patch합니다.
        # 'app.services.twitter_collector.tweepy.Client'는 TwitterCollector 클래스 내에서
        # tweepy.Client가 임포트되는 경로를 정확히 지정해야 합니다.
        # 즉, 'app.services.twitter_collector' 모듈 안의 'tweepy.Client'를 패치합니다.
        patcher = patch('app.services.twitter_collector.tweepy.Client')
        self.mock_client_class = patcher.start()
        self.addCleanup(patcher.stop) # 테스트 종료 후 patch를 정리합니다.

        self.mock_client_instance = self.mock_client_class.return_value
        self.collector = TwitterCollector()

    def test_get_user_id_by_username_success(self):
        """
        사용자 이름으로 사용자 ID를 성공적으로 가져오는 경우를 테스트합니다.
        """
        # X API의 get_user 응답 데이터를 모의(Mock)합니다.
        mock_response_data = MagicMock()
        mock_response_data.id = "1234567890"
        mock_response_data.username = "testuser"
        mock_response = MagicMock(data=mock_response_data)
        self.mock_client_instance.get_user.return_value = mock_response

        user_id = self.collector.get_user_id_by_username("testuser")
        self.assertEqual(user_id, "1234567890")
        # get_user 메서드가 올바른 인수로 한 번 호출되었는지 확인합니다.
        self.mock_client_instance.get_user.assert_called_once_with(username="testuser")

    def test_get_user_id_by_username_not_found(self):
        """
        사용자 이름이 존재하지 않아 API 응답에 데이터가 없는 경우를 테스트합니다.
        """
        # API 응답에 데이터가 없는 경우를 모의합니다.
        mock_response = MagicMock(data=None)
        self.mock_client_instance.get_user.return_value = mock_response

        user_id = self.collector.get_user_id_by_username("nonexistentuser")
        self.assertIsNone(user_id)
        self.mock_client_instance.get_user.assert_called_once_with(username="nonexistentuser")

    def test_get_user_id_by_username_api_error(self):
        """
        사용자 ID를 가져오는 중 API 오류(TweepyException)가 발생하는 경우를 테스트합니다.
        """
        # TweepyException 발생을 모의합니다.
        self.mock_client_instance.get_user.side_effect = tweepy.TweepyException("API Error: User not found")

        user_id = self.collector.get_user_id_by_username("erroruser")
        self.assertIsNone(user_id)
        self.mock_client_instance.get_user.assert_called_once_with(username="erroruser")

    def test_get_recent_tweets_by_user_id_success(self):
        """
        사용자 ID로 최신 트윗을 성공적으로 가져오는 경우를 테스트합니다.
        """
        # 트윗 데이터 및 public_metrics를 포함한 응답 데이터를 모의합니다.
        mock_tweet1 = MagicMock(
            id="1",
            text="Test tweet 1",
            created_at=MagicMock(isoformat=lambda: "2023-01-01T10:00:00Z"), # isoformat() 메서드 모의
            public_metrics={'retweet_count': 10, 'reply_count': 1, 'like_count': 100, 'quote_count': 5}
        )
        mock_tweet2 = MagicMock(
            id="2",
            text="Test tweet 2",
            created_at=MagicMock(isoformat=lambda: "2023-01-02T11:00:00Z"),
            public_metrics={'retweet_count': 5, 'reply_count': 0, 'like_count': 50, 'quote_count': 2}
        )
        mock_response = MagicMock(data=[mock_tweet1, mock_tweet2])
        self.mock_client_instance.get_users_tweets.return_value = mock_response

        tweets = self.collector.get_recent_tweets_by_user_id("12345", max_results=2)
        self.assertEqual(len(tweets), 2)
        self.assertEqual(tweets[0]['id'], "1")
        self.assertEqual(tweets[0]['text'], "Test tweet 1")
        self.assertEqual(tweets[0]['created_at'], "2023-01-01T10:00:00Z")
        self.assertEqual(tweets[0]['like_count'], 100)
        self.assertEqual(tweets[1]['id'], "2")
        self.assertEqual(tweets[1]['text'], "Test tweet 2")
        self.assertEqual(tweets[1]['created_at'], "2023-01-02T11:00:00Z")
        self.assertEqual(tweets[1]['like_count'], 50)
        # get_users_tweets 메서드가 올바른 인수로 한 번 호출되었는지 확인합니다.
        self.mock_client_instance.get_users_tweets.assert_called_once_with(
            id="12345", max_results=2, tweet_fields=["created_at", "public_metrics"]
        )

    def test_get_recent_tweets_by_user_id_no_tweets(self):
        """
        사용자에게 트윗이 없는 경우를 테스트합니다.
        """
        # 트윗 데이터가 없는 경우를 모의합니다.
        mock_response = MagicMock(data=None)
        self.mock_client_instance.get_users_tweets.return_value = mock_response

        tweets = self.collector.get_recent_tweets_by_user_id("54321", max_results=5)
        self.assertEqual(len(tweets), 0)
        self.mock_client_instance.get_users_tweets.assert_called_once_with(
            id="54321", max_results=5, tweet_fields=["created_at", "public_metrics"]
        )

    def test_get_recent_tweets_by_user_id_api_error(self):
        """
        트윗을 가져오는 중 API 오류(TweepyException)가 발생하는 경우를 테스트합니다.
        """
        # TweepyException 발생을 모의합니다.
        self.mock_client_instance.get_users_tweets.side_effect = tweepy.TweepyException("Tweet API Error")

        tweets = self.collector.get_recent_tweets_by_user_id("error_id", max_results=1)
        self.assertEqual(len(tweets), 0)
        self.mock_client_instance.get_users_tweets.assert_called_once_with(
            id="error_id", max_results=1, tweet_fields=["created_at", "public_metrics"]
        )

# 이 파일을 직접 실행할 때 단위 테스트를 시작합니다.
if __name__ == '__main__':
    unittest.main()
