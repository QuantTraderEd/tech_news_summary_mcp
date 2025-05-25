import logging
import traceback
import nest_asyncio # nest_asyncio 임포트

import twint
import pandas as pd

def get_user_tweets_with_twint(username: str, max_tweets: int = 100):
    """
    Twint 라이브러리를 사용하여 특정 트위터 유저의 게시글을 조회합니다.

    Args:
        username (str): 조회할 트위터 사용자 이름 (예: 'elonmusk').
        max_tweets (int): 가져올 최대 게시글 수.

    Returns:
        pd.DataFrame: 조회된 게시글 데이터를 담고 있는 DataFrame.
                      게시글이 없거나 오류 발생 시 빈 DataFrame을 반환합니다.
    """
    # Twint 설정 객체를 생성합니다.
    c = twint.Config()

    # 스크랩할 사용자 이름을 설정합니다.
    c.Username = username
    # 가져올 최대 트윗 수를 설정합니다.
    c.Limit = max_tweets
    # 트윗 데이터를 Pandas DataFrame으로 저장하도록 설정합니다.
    c.Pandas = True
    # 트윗 내용을 출력하지 않도록 설정합니다 (데이터프레임으로 가져올 것이므로).
    c.Hide_output = True
    # HTTP 요청 헤더에 사용자 에이전트(User-Agent)를 설정합니다.
    # 이는 서버가 요청을 더 "정상적인" 웹 브라우저 요청으로 인식하도록 도울 수 있습니다.
    c.User_Agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    # c.Proxy_host = "127.0.0.1" # 프록시가 필요한 경우 설정
    # c.Proxy_port = 8080
    # c.Proxy_type = "http"


    tweets_df = pd.DataFrame() # 결과를 저장할 빈 DataFrame 초기화

    try:
        # Twint를 실행하여 트윗을 스크랩합니다.
        twint.run.Search(c)

        # Twint가 스크랩한 데이터를 Pandas DataFrame으로 가져옵니다.
        # twint.output.panda.Tweets_df는 스크랩된 트윗 데이터를 포함합니다.
        if twint.output.panda.Tweets_df is not None:
            tweets_df = twint.output.panda.Tweets_df
            # 필요한 컬럼만 선택하거나 이름을 변경할 수 있습니다.
            # 예시: 'date', 'id', 'tweet', 'username', 'link', 'nlikes', 'nretweets'
            selected_columns = [
                'date', 'id', 'tweet', 'username', 'link', 'nlikes', 'nretweets'
            ]
            # 모든 컬럼이 존재하는지 확인하고, 없는 컬럼은 제외합니다.
            existing_columns = [col for col in selected_columns if col in tweets_df.columns]
            tweets_df = tweets_df[existing_columns]
            # 컬럼 이름을 더 읽기 쉽게 변경합니다.
            tweets_df = tweets_df.rename(columns={
                'date': 'Date',
                'id': 'Tweet ID',
                'tweet': 'Content',
                'username': 'Username',
                'link': 'URL',
                'nlikes': 'Likes',
                'nretweets': 'Retweets'
            })
            return tweets_df
        else:
            print(f"'{username}' 유저의 게시글을 찾을 수 없습니다. (데이터프레임이 비어있음)")
            return pd.DataFrame()

    except Exception as e:
        print(f"오류 발생: {e}")
        print(traceback.format_exc())
        print("\n--- Twint 오류 해결 가이드 ---")
        print("1. 'Header value is too long' 오류는 주로 HTTP 요청 헤더(특히 쿠키)가 너무 커서 발생합니다.")
        print("   Twint는 비공식 API를 사용하므로 트위터 웹사이트 변경에 매우 취약합니다.")
        print("2. Twint 라이브러리를 GitHub에서 최신 버전으로 재설치했는지 확인하세요:")
        print("   pip uninstall twint aiohttp")
        print("   pip install --user --upgrade git+https://github.com/twintproject/twint.git#egg=twint")
        print("   pip install aiohttp # 또는 pip install aiohttp==3.8.1")
        print("3. 코드 시작 부분에 'import nest_asyncio'와 'nest_asyncio.apply()'를 추가했는지 확인하세요.")
        print("4. 이 문제가 계속 발생한다면, 더 안정적인 대안인 'snscrape' 라이브러리 사용을 강력히 권장합니다.")
        print("----------------------------")
        return pd.DataFrame() # 오류 발생 시 빈 DataFrame 반환

if __name__ == "__main__":
    # nest_asyncio 적용 (메인 함수 시작 부분에 위치)
    nest_asyncio.apply()

    # 예시: 'TwitterDev' 유저의 최신 게시글 10개 조회
    target_username = 'TwitterDev'
    num_tweets_to_fetch = 10

    print(f"'{target_username}' 유저의 최신 게시글 {num_tweets_to_fetch}개를 Twint로 조회합니다...")
    user_tweets_df = get_user_tweets_with_twint(target_username, num_tweets_to_fetch)

    if not user_tweets_df.empty:
        print(f"\n'{target_username}' 유저의 게시글 조회 결과 (상위 5개):")
        print(user_tweets_df.head())

        # 모든 게시글을 CSV 파일로 저장 (선택 사항)
        # user_tweets_df.to_csv(f'{target_username}_twint_tweets.csv', index=False, encoding='utf-8-sig')
        # print(f"\n게시글이 '{target_username}_twint_tweets.csv' 파일로 저장되었습니다.")
    else:
        print(f"'{target_username}' 유저의 게시글을 가져오지 못했습니다.")

    print("\n--- 다른 유저 예시 ---")
    target_username_2 = 'elonmusk'
    num_tweets_to_fetch_2 = 10
    print(f"'{target_username_2}' 유저의 최신 게시글 {num_tweets_to_fetch_2}개를 Twint로 조회합니다...")
    user_tweets_df_2 = get_user_tweets_with_twint(target_username_2, num_tweets_to_fetch_2)

    if not user_tweets_df_2.empty:
        print(f"\n'{target_username_2}' 유저의 게시글 조회 결과 (상위 5개):")
        print(user_tweets_df_2.head())
    else:
        print(f"'{target_username_2}' 유저의 게시글을 가져오지 못했습니다.")
