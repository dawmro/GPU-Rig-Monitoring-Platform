#!/bin/bash
# GPU Rig Monitoring Agent - Install Script
# Run as root on the target rig

set -euo pipefail

INSTALL_DIR="/opt/monitoring-agent"
CONFIG_DIR="/etc/monitoring-agent"
LOG_DIR="/var/log/monitoring-agent"
LOCK_DIR="/var/lock"
CRON_FILE="/etc/cron.d/monitoring-agent"
SERVICE_USER="monitoring-agent"

echo "=== GPU Rig Monitoring Agent Installer ==="

# Create service user
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
    echo "Created user: $SERVICE_USER"
fi

# Create directories
mkdir -p "$INSTALL_DIR" "$CONFIG_DIR" "$LOG_DIR"
chown "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR"

# Set up Python virtual environment
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip 2>/dev/null || true
"$INSTALL_DIR/venv/bin/pip" install psutil py-cpuinfo requests pyyaml docker

# Try to install pynvml (NVIDIA GPU monitoring)
"$INSTALL_DIR/venv/bin/pip" install nvidia-ml-py3 2>/dev/null || \
    echo "WARNING: pynvml not installed. GPU monitoring will be unavailable."

# Copy agent files
cp run.py "$INSTALL_DIR/run.py"
chmod +x "$INSTALL_DIR/run.py"

# Config file
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp config.yaml.example "$CONFIG_DIR/config.yaml"
    chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/config.yaml"
    chmod 600 "$CONFIG_DIR/config.yaml"
    echo "Created config template at $CONFIG_DIR/config.yaml"
    echo "⚠️  Edit this file with your API key and server endpoint!"
fi

# Sudoers for hardware access — only commands the agent actually uses
# IMPORTANT: The !authenticate default is required for system users with nologin shell.
# Without it, PAM pam_unix auth fails with "could not identify password" even with NOPASSWD.
cat > /etc/sudoers.d/monitoring-agent << 'EOF'
Defaults:monitoring-agent !authenticate
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/smartctl, /bin/journalctl, /usr/bin/journalctl, /usr/sbin/nvme, /usr/bin/nvme, /usr/bin/docker, /usr/local/bin/docker
EOF
chmod 440 /etc/sudoers.d/monitoring-agent

# Cron job
cat > "$CRON_FILE" << EOF
# GPU Rig Monitoring Agent - runs every 60 seconds
* * * * * $SERVICE_USER flock -n $LOCK_DIR/monitoring-agent.lock $INSTALL_DIR/venv/bin/python $INSTALL_DIR/run.py >> $LOG_DIR/cron.log 2>&1
EOF
chmod 644 "$CRON_FILE"

# Auto-update check — random time once per day to prevent thundering herd
HOUR=$((RANDOM % 24))
MINUTE=$((RANDOM % 60))
UPDATE_CRON_FILE="/etc/cron.d/monitoring-agent-update"
cat > "$UPDATE_CRON_FILE" << EOF
# GPU Rig Monitoring Agent — Auto-update check (daily at ${HOUR}:${MINUTE})
${MINUTE} ${HOUR} * * * $SERVICE_USER $INSTALL_DIR/venv/bin/python $INSTALL_DIR/check_update.py >> $LOG_DIR/update.log 2>&1
EOF
chmod 644 "$UPDATE_CRON_FILE"
# Also copy check_update.py
cp check_update.py "$INSTALL_DIR/check_update.py"
chmod +x "$INSTALL_DIR/check_update.py"
echo "Auto-update: daily check scheduled at $(printf '%02d:%02d' $HOUR $MINUTE)"

# Data cleanup — random time daily (2-4 AM) to prevent thundering herd
CLONE_HOUR=$((2 + RANDOM % 3))
CLONE_MINUTE=$((RANDOM % 60))
CLEANUP_CRON_FILE="/etc/cron.d/monitoring-data-cleanup"
cat > "$CLEANUP_CRON_FILE" << EOF
# GPU Rig Monitoring Platform — Data retention cleanup (daily at ${CLONE_HOUR}:${CLONE_MINUTE})
${CLONE_MINUTE} ${CLONE_HOUR} * * * root bash /opt/gpu_monitor/deploy/data_retention.sh >> /var/log/monitoring-agent/cleanup-cron.log 2>&1
EOF
chmod 644 "$CLEANUP_CRON_FILE"
# Copy cleanup script
cp gpu_monitor/deploy/data_retention.sh "$OPT/gpu_monitor/deploy/data_retention.sh"
chmod +x "$OPT/gpu_monitor/deploy/data_retention.sh"
echo "Data cleanup: daily at $(printf '%02d:%02d' $CLONE_HOUR $CLONE_MINUTE)"

echo ""
echo "=== Installation Complete ==="
echo "Config:    $CONFIG_DIR/config.yaml"
echo "Logs:      $LOG_DIR/agent.log"
echo "Agent:     $INSTALL_DIR/run.py"
echo "Update log: $LOG_DIR/update.log"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/config.yaml with your API key"
echo "  2. Test: sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/python $INSTALL_DIR/run.py"
echo "  3. The cron job will start automatically within 1 minute"
