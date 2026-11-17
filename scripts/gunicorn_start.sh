#!/bin/bash

echo "Executing generate_config.sh..."
. $PWD/scripts/generate_config.sh

# 워커 2개(1개는 예비). --preload: master 가 앱을 한 번 import 한 뒤 워커를 복제하므로,
# 모듈 import 시점에 네트워크 연결이나 스레드를 만들면 안 된다.
gunicorn src.main:app -k uvicorn.workers.UvicornWorker -w 2 --preload -b 0.0.0.0:8080 --timeout 1800