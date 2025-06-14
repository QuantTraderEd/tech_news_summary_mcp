import os
import sys
import site
import json
import pprint
import requests


src_path = os.path.dirname(__file__)
pjt_home_path = os.path.join(src_path, os.pardir)
pjt_home_path = os.path.abspath(pjt_home_path)

site.addsitedir(pjt_home_path)

# config.json 파일에서 API 키 불러오기
with open(f'{pjt_home_path}/config.json', 'r') as config_file:
    config = json.load(config_file)
    token_key = config.get("BEARER_TOKEN")

bearer_token = token_key

def bearer_oauth(r):
    """
    Bearer Token 으로 oauth 권한 받음!! r을 리턴해서 이후 header에 넣는다!!
    """
    r.headers["Authorization"] = f"Bearer {bearer_token}"
    r.headers["User-Agent"] = "v2UserLookupPython"
    return r

## 여기에 사용자 아이디입력!! :
usernames = "usernames=rwang07"
user_fields = "user.fields=description,created_at"

user_info_url = f"https://api.twitter.com/2/users/by?{usernames}&{user_fields}"

# response = requests.request("GET", user_info_url, auth=bearer_oauth,)
# print(response.status_code)
#
# json_response = response.json()
# pprint.pprint(json_response)
#
# if response.status_code != 200:
#     sys.exit(1)

# user_id = json_response['data'][0]['id']
user_id = '833295407182516224'   # raywang
user_tweets_url = f"https://api.twitter.com/2/users/{user_id}/tweets"

target_params = {
    "tweet.fields": "created_at",
    "max_results": 100,  #  갯수 설정 가능!!  Required range: 5 <= x <= 100
    "exclude": ["retweets"],
}

response = requests.request("GET", user_tweets_url, auth=bearer_oauth, params=target_params)
print(response.status_code)

json_response =  response.json()
# pprint.pprint(json_response)
if response.status_code == 200:
    with open(f'{pjt_home_path}/data/raywang_tweets.json', 'w', encoding='utf-8') as f:
        json.dump(json_response, f, ensure_ascii=False, indent=2)
else:
    pprint.pprint(json_response)
