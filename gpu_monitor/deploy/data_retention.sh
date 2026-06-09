#!/bin/bash
# GPU Rig Monitoring Platform — Daily data retention cleanup
# Runs compact_data and cleanup_old_data management commands
# Designed to be called by cron

OPT="/opt/gpu_monitor"
LOG_DIR="/var/log/monitoring-agent"
RETENTION_DAYS="${1:-31}"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

cd "$OPT"
source venv/bin/activate
set -a && source .env && set +a

echo "=== Data retention cleanup $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_DIR/cleanup.log"

# Phase 1: Compact old data (continue on error)
echo "Compacting data..." >> "$LOG_DIR/cleanup.log"
python manage.py compact_data --verbose >> "$LOG_DIR/cleanup.log" 2>&1 || true

# Phase 2: Delete data older than retention period
echo "Cleaning up data older than ${RETENTION_DAYS} days..." >> "$LOG_DIR/cleanup.log"
python manage.py cleanup_old_data --days="$RETENTION_DAYS" >> "$LOG_DIR/cleanup.log" 2>&1 || true

echo "=== Cleanup complete $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_DIR/cleanup.log"
