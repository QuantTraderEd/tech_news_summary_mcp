#!/bin/bash

# news scrapping
echo 'start news scrapping...' 

python3 $PWD/src/services/news_crawler_zdnet.py 반도체 &&
python3 $PWD/src/services/news_crawler_zdnet.py 자동차 &&
python3 $PWD/src/services/news_crawler_zdnet.py 배터리 &&
python3 $PWD/src/services/news_crawler_zdnet.py 컴퓨팅 &&

python3 $PWD/src/services/news_crawler_thelec.py 반도체 &&
python3 $PWD/src/services/news_crawler_thelec.py 자동차 &&
python3 $PWD/src/services/news_crawler_thelec.py 배터리 &&

python3 $PWD/src/services/news_crawler_etnews.py 전자 &&
python3 $PWD/src/services/news_crawler_etnews.py SW &&
python3 $PWD/src/services/news_crawler_etnews.py IT &&

echo 'finish news scrapping!!'

# upload data json file to gcs
echo 'upload data json file to gcs...'
python3 $PWD/src/services/gcs_upload_json.py zdnet &&
python3 $PWD/src/services/gcs_upload_json.py thelec &&
python3 $PWD/src/services/gcs_upload_json.py etnews &&
echo 'finish upload!!'

# news summary
python3 $PWD/src/services/news_summarizer.py


