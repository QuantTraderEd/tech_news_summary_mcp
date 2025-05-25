import snscrape.modules.twitter as sntwitter
import pandas as pd

def get_user_tweets(username: str, max_tweets: int = 100):
    """
    특정 트위터 유저의 게시글을 조회합니다.

    Args:
        username (str): 조회할 트위터 사용자 이름 (예: 'elonmusk').
        max_tweets (int): 가져올 최대 게시글 수.

    Returns:
        pd.DataFrame: 조회된 게시글 데이터를 담고 있는 DataFrame.
                      게시글이 없거나 오류 발생 시 빈 DataFrame을 반환합니다.
    """
    tweets_list = []
    try:
        # snscrape를 사용하여 특정 유저의 트윗을 스크랩합니다.
        # 'from:username' 쿼리를 사용하여 특정 유저의 트윗만 필터링합니다.
        for i, tweet in enumerate(sntwitter.TwitterSearchScraper(f'from:{username}').get_items()):
            if i >= max_tweets:
                break
            tweets_list.append([
                tweet.date,
                tweet.id,
                tweet.content,
                tweet.user.username,
                tweet.url,
                tweet.likeCount,
                tweet.retweetCount
            ])

        # 스크랩된 데이터를 Pandas DataFrame으로 변환합니다.
        tweets_df = pd.DataFrame(tweets_list, columns=[
            'Date', 'Tweet ID', 'Content', 'Username', 'URL', 'Likes', 'Retweets'
        ])
        return tweets_df

    except Exception as e:
        print(f"오류 발생: {e}")
        print("snscrape 라이브러리 설치를 확인하거나, 사용자 이름이 올바른지 확인해주세요.")
        return pd.DataFrame() # 오류 발생 시 빈 DataFrame 반환

if __name__ == "__main__":
    # 예시: 'TwitterDev' 유저의 최신 게시글 50개 조회
    target_username = 'dnystedt'
    num_tweets_to_fetch = 50

    print(f"'{target_username}' 유저의 최신 게시글 {num_tweets_to_fetch}개를 조회합니다...")
    user_tweets_df = get_user_tweets(target_username, num_tweets_to_fetch)

    if not user_tweets_df.empty:
        print(f"\n'{target_username}' 유저의 게시글 조회 결과 (상위 5개):")
        print(user_tweets_df.head())

        # 모든 게시글을 CSV 파일로 저장 (선택 사항)
        # user_tweets_df.to_csv(f'{target_username}_tweets.csv', index=False, encoding='utf-8-sig')
        # print(f"\n게시글이 '{target_username}_tweets.csv' 파일로 저장되었습니다.")
    else:
        print(f"'{target_username}' 유저의 게시글을 가져오지 못했습니다.")

    print("\n--- 다른 유저 예시 ---")
    target_username_2 = 'elonmusk'
    num_tweets_to_fetch_2 = 10
    print(f"'{target_username_2}' 유저의 최신 게시글 {num_tweets_to_fetch_2}개를 조회합니다...")
    user_tweets_df_2 = get_user_tweets(target_username_2, num_tweets_to_fetch_2)

    if not user_tweets_df_2.empty:
        print(f"\n'{target_username_2}' 유저의 게시글 조회 결과 (상위 5개):")
        print(user_tweets_df_2.head())
    else:
        print(f"'{target_username_2}' 유저의 게시글을 가져오지 못했습니다.")
