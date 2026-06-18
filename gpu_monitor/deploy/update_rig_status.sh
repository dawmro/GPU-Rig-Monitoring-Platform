#!/bin/bash
# Rig status update wrapper — called by cron every 2 minutes
cd /opt/gpu_monitor
. venv/bin/activate
# Source .env safely — export only KEY=VALUE lines, skip comments and empty lines
while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    key=$(echo "$key" | xargs)
    export "$key=$value"
done < .env
python manage.py update_rig_status >> /opt/gpu_monitor/logs/rig_status.log 2>&1
