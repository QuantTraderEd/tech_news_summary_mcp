#!/bin/bash

# generate_config.sh 실행
# echo "Executing generate_config.sh..."
# . $PWD/scripts/generate_config.sh

# tweet post scrapping 실행
echo "Executing tweet_scrapper_post.py"
python3 $PWD/app/services/tweet_scrapper_post.py

echo "tweet_scrapper_post.py completed."

# tweet summary 실행
echo "Executing tweet_summarizer.py"
python3 $PWD/app/services/tweet_summarizer.py

echo "tweet_summarizer.py completed."

# send result of summary
python3 $PWD/app/services/send_mail_tweet.py $NVR_MAIL_PWD

echo "send_mail_tweet.py completed.!"