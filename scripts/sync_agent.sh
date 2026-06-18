#!/bin/bash
# Sync Linux monitoring agent from workspace to /opt
#
# Copies the latest agent/run.py to /opt/monitoring-agent/run.py
# and ensures the payload.json log directory exists.
#
# Usage:
#   sudo bash scripts/sync_agent.sh [USER]
#   USER defaults to SUDO_USER or LOGNAME

# Determine workspace user: argument > SUDO_USER > LOGNAME > current user
SYNC_USER="${1:-${SUDO_USER:-${LOGNAME:-$(whoami)}}}"
WORKSPACE="/home/$SYNC_USER/workspace/GPU-Rig-Monitoring-Platform"
OPT="/opt"

set -e
echo "=== Syncing Linux agent $WORKSPACE/agent -> $OPT/monitoring-agent ---"

if [ -f "$WORKSPACE/agent/run.py" ]; then
    cp "$WORKSPACE/agent/run.py" "$OPT/monitoring-agent/run.py"
    chmod +x "$OPT/monitoring-agent/run.py"
    echo "  Synced: agent/run.py -> $OPT/monitoring-agent/run.py"
else
    echo "  ERROR: $WORKSPACE/agent/run.py not found"
    exit 1
fi

echo "=== Done ==="
