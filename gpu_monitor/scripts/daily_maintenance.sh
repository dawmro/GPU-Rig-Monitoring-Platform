#!/bin/bash
# GPU Rig Monitoring Platform — Daily Database Maintenance
# Run via cron: 0 3 * * * /opt/gpu_monitor/scripts/daily_maintenance.sh

set -e

cd /opt/gpu_monitor
source venv/bin/activate
# Source .env safely — export only KEY=VALUE lines, skip comments and empty lines
while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    key=$(echo "$key" | xargs)
    export "$key=$value"
done < .env

echo "=== Daily Maintenance: $(date '+%Y-%m-%d %H:%M:%S') ==="

# Run Django maintenance command
python manage.py daily_maintenance --verbose

echo "=== Maintenance complete: $(date '+%Y-%m-%d %H:%M:%S') ==="
