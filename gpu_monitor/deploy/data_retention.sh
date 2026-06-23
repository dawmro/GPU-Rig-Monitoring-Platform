#!/bin/bash
# GPU Rig Monitoring Platform — Daily data retention cleanup
# Runs compact_data, cleanup_old_data, and VACUUM ANALYZE
# Designed to be called by cron

OPT="/opt/gpu_monitor"
LOG_DIR="/opt/gpu_monitor/logs"
RETENTION_DAYS="${1:-31}"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

cd "$OPT"
source venv/bin/activate
# Source .env safely — export only KEY=VALUE lines, skip comments and empty lines
while IFS='=' read -r key value; do
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    key=$(echo "$key" | xargs)
    export "$key=$value"
done < .env

echo "=== Data retention cleanup $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_DIR/cleanup.log"

# Check disk space
DISK_USAGE=$(df /opt | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 80 ]; then
    echo "WARNING: Disk usage at ${DISK_USAGE}%" >> "$LOG_DIR/cleanup.log"
fi

# Phase 1: Compact old data (continue on error)
echo "Compacting data..." >> "$LOG_DIR/cleanup.log"
python manage.py compact_data --verbose >> "$LOG_DIR/cleanup.log" 2>&1 || true

# Phase 2: Delete data older than retention period
echo "Cleaning up data older than ${RETENTION_DAYS} days..." >> "$LOG_DIR/cleanup.log"
python manage.py cleanup_old_data --days="$RETENTION_DAYS" >> "$LOG_DIR/cleanup.log" 2>&1 || true

# Phase 3: VACUUM ANALYZE on metrics tables
# Reclaims dead tuples and updates planner statistics after bulk DELETEs
echo "Running VACUUM ANALYZE..." >> "$LOG_DIR/cleanup.log"
sudo -u postgres psql -d gpu_monitor -c "
  VACUUM ANALYZE metrics_gpumetric;
  VACUUM ANALYZE metrics_storagemetric;
  VACUUM ANALYZE metrics_networkmetric;
  VACUUM ANALYZE metrics_gpu_process;
  VACUUM ANALYZE metrics_power_reading;
  VACUUM ANALYZE metrics_metricsnapshot;
" >> "$LOG_DIR/cleanup.log" 2>&1 || true

# Phase 4: Clean up old audit log entries (90-day retention)
echo "Cleaning up audit logs older than 90 days..." >> "$LOG_DIR/cleanup.log"
python manage.py cleanup_audit_log --days=90 >> "$LOG_DIR/cleanup.log" 2>&1 || true

echo "=== Cleanup complete $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_DIR/cleanup.log"
