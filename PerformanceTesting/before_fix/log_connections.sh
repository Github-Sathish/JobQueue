#!/bin/bash
OUTFILE=$1
echo "timestamp,total_connections,active,idle" > "$OUTFILE"
while true; do
  ts=$(date '+%Y-%m-%d %H:%M:%S')
  total=$(sudo -u postgres psql -d jobqueue_db -tAc "SELECT count(*) FROM pg_stat_activity;")
  active=$(sudo -u postgres psql -d jobqueue_db -tAc "SELECT count(*) FROM pg_stat_activity WHERE state='active';")
  idle=$(sudo -u postgres psql -d jobqueue_db -tAc "SELECT count(*) FROM pg_stat_activity WHERE state='idle';")
  echo "$ts,$total,$active,$idle" >> "$OUTFILE"
  sleep 2
done
