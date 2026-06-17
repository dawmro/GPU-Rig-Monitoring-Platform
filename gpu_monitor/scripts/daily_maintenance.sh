#!/bin/bash
# GPU Rig Monitoring Platform — Daily Database Maintenance
# Run via cron: 0 3 * * * /opt/gpu_monitor/scripts/daily_maintenance.sh

set -e

cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

echo "=== Daily Maintenance: $(date '+%Y-%m-%d %H:%M:%S') ==="

# Run Django maintenance command
python manage.py daily_maintenance --verbose

echo "=== Maintenance complete: $(date '+%Y-%m-%d %H:%M:%S') ==="
