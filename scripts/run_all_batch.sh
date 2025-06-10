#!/bin/bash

# start_batch.sh 실행
echo "Executing start_batch.sh..."
. $PWD/scripts/start_batch.sh

echo "start_batch.sh completed."

# send_summary.sh 실행
echo "Executing send_summary.sh..."
. $PWD/scripts/send_summary.sh

echo "send_summary.sh completed."

echo "All batch jobs completed successfully."
