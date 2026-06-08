#!/bin/bash
# GPU Rig Monitoring Platform — Daily data retention cleanup
# Runs compact_data and cleanup_old_data management commands
# Designed to be called by cron

set -euo pipefail

OPT="/opt/gpu_monitor"
LOG_DIR="/var/log/monitoring-agent"
RETENTION_DAYS="${1:-31}"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

# Timestamp for logging
echo "=== Data retention cleanup $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_DIR/cleanup.log"

# Source environment and activate venv
cd "$OPT"
source venv/bin/activate
set -a && source .env && set +a

# Phase 1: Compact old data
echo "Compacting data..." >> "$LOG_DIR/cleanup.log"
python manage.py compact_data --verbose >> "$LOG_DIR/cleanup.log" 2>&1

# Phase 2: Delete data older than retention period
echo "Cleaning up data older than ${RETENTION_DAYS} days..." >> "$LOG_DIR/cleanup.log"
python manage.py cleanup_old_data --days="$RETENTION_DAYS" --verbose >> "$LOG_DIR/cleanup.log" 2>&1

echo "=== Cleanup complete $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_DIR/cleanup.log"
