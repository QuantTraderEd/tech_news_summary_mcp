#!/bin/bash

echo "Executing generate_config.sh..."
. $PWD/scripts/generate_config.sh

gunicorn src.main:app -k uvicorn.workers.UvicornWorker -w 4  -b 0.0.0.0:8080 --timeout 1800