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
source "$INSTALL_DIR/venv/bin/upgrade-pip"
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
    chmod 600 "$CONFIG_DIR/config.yaml"
    echo "Created config template at $CONFIG_DIR/config.yaml"
    echo "⚠️  Edit this file with your API key and server endpoint!"
fi

# Suders for SMART/NVMe access
cat > /etc/sudoers.d/monitoring-agent << 'EOF'
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/nvme, /bin/journalctl, /usr/bin/systemctl list-units
EOF
chmod 440 /etc/sudoers.d/monitoring-agent

# Cron job
cat > "$CRON_FILE" << EOF
# GPU Rig Monitoring Agent - runs every 60 seconds
* * * * * $SERVICE_USER flock -n $LOCK_DIR/monitoring-agent.lock $INSTALL_DIR/venv/bin/python $INSTALL_DIR/run.py >> $LOG_DIR/cron.log 2>&1
EOF
chmod 644 "$CRON_FILE"

echo ""
echo "=== Installation Complete ==="
echo "Config: $CONFIG_DIR/config.yaml"
echo "Logs:   $LOG_DIR/agent.log"
echo "Agent:  $INSTALL_DIR/run.py"
echo ""
echo "Next steps:"
echo "  1. Edit $CONFIG_DIR/config.yaml with your API key"
echo "  2. Test: sudo -u $SERVICE_USER $INSTALL_DIR/venv/bin/python $INSTALL_DIR/run.py"
echo "  3. The cron job will start automatically within 1 minute"
