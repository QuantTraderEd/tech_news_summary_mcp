import tweepy
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional
import logging

# 로깅 설정
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TwitterCollector:
    def __init__(self):
        load_dotenv() # .env 파일에서 환경 변수 로드
        self.bearer_token = os.getenv("X_BEARER_TOKEN")
        if not self.bearer_token:
            raise ValueError("X_BEARER_TOKEN environment variable is not set.")
        try:
            self.client = tweepy.Client(self.bearer_token)
            logger.info("Tweepy client initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Tweepy client: {e}")
            raise

    def get_user_id_by_username(self, username: str) -> Optional[str]:
        """
        주어진 사용자 이름으로 X 사용자 ID를 가져옵니다.
        """
        try:
            response = self.client.get_user(username=username)
            if response.data:
                logger.info(f"Found user ID for @{username}: {response.data.id}")
                return str(response.data.id)
            else:
                logger.warning(f"User @{username} not found or no data returned.")
                return None
        except tweepy.TweepyException as e:
            logger.error(f"Tweepy API error getting user ID for @{username}: {e}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred getting user ID for @{username}: {e}")
            return None

    def get_recent_tweets_by_user_id(self, user_id: str, max_results: int = 10) -> List[Dict[str, str]]:
        """
        주어진 사용자 ID의 최신 트윗을 가져옵니다.
        max_results는 X API 정책에 따라 제한될 수 있습니다.
        """
        tweets_data = []
        try:
            # tweet_fields를 사용하여 필요한 필드를 지정할 수 있습니다.
            # 예: created_at, public_metrics 등
            response = self.client.get_users_tweets(
                id=user_id,
                max_results=max_results,
                tweet_fields=["created_at", "public_metrics"]
            )

            if response.data:
                for tweet in response.data:
                    tweets_data.append({
                        "id": str(tweet.id),
                        "text": tweet.text,
                        "created_at": tweet.created_at.isoformat() if tweet.created_at else None,
                        "retweet_count": tweet.public_metrics.get('retweet_count', 0) if tweet.public_metrics else 0,
                        "reply_count": tweet.public_metrics.get('reply_count', 0) if tweet.public_metrics else 0,
                        "like_count": tweet.public_metrics.get('like_count', 0) if tweet.public_metrics else 0,
                        "quote_count": tweet.public_metrics.get('quote_count', 0) if tweet.public_metrics else 0,
                        "url": f"https://x.com/i/web/status/{tweet.id}" # 트윗 직접 링크 (사용자 이름은 동적으로 추가해야 할 수도 있음)
                    })
                logger.info(f"Successfully fetched {len(tweets_data)} tweets for user ID {user_id}.")
            else:
                logger.warning(f"No tweets found for user ID {user_id}.")
        except tweepy.TweepyException as e:
            logger.error(f"Tweepy API error getting tweets for user ID {user_id}: {e}")
        except Exception as e:
            logger.error(f"An unexpected error occurred getting tweets for user ID {user_id}: {e}")
        return tweets_data

# 예시 사용법 (로컬 테스트용)
if __name__ == "__main__":
    collector = TwitterCollector()
    target_username = "dnystedt" # 크롤링할 트위터 사용자 이름

    user_id = collector.get_user_id_by_username(target_username)
    if user_id:
        print(f"\nFetching recent tweets for @{target_username} (ID: {user_id})...")
        tweets = collector.get_recent_tweets_by_user_id(user_id, max_results=5) # 최신 5개 트윗
        if tweets:
            for i, tweet in enumerate(tweets):
                print(f"\n--- Tweet {i+1} ---")
                print(f"ID: {tweet['id']}")
                print(f"Text: {tweet['text']}")
                print(f"Created At: {tweet['created_at']}")
                print(f"URL: {tweet['url']}")
        else:
            print(f"No tweets found for @{target_username}.")
    else:
        print(f"Could not retrieve user ID for @{target_username}.")