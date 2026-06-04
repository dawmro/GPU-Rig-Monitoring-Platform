#!/bin/bash
# Sync monitoring agents from workspace to /opt
#
# Copies the latest agent run.py files to their production locations.
# Safe to run repeatedly.
#
# Usage:
#   bash scripts/sync_agents.sh

WORKSPACE="/home/qrv/workspace/GPU-Rig-Monitoring-Platform"
OPT="/opt"

set -e
echo "=== Syncing agents $WORKSPACE -> $OPT ==="

# ── Linux agent ──────────────────────────────────────────────────────
echo "--- Agent (Linux) ---"
if [ -f "$WORKSPACE/agent/run.py" ]; then
    cp "$WORKSPACE/agent/run.py" "$OPT/monitoring-agent/run.py"
    chmod +x "$OPT/monitoring-agent/run.py"
    echo "  Synced: agent/run.py -> $OPT/monitoring-agent/run.py"
else
    echo "  WARNING: $WORKSPACE/agent/run.py not found"
fi

# ── Windows agent ────────────────────────────────────────────────────
echo "--- Agent (Windows) ---"
if [ -f "$WORKSPACE/agent_windows/run.py" ]; then
    mkdir -p "$OPT/agent_windows"
    cp "$WORKSPACE/agent_windows/run.py" "$OPT/agent_windows/run.py"
    chmod +x "$OPT/agent_windows/run.py"
    echo "  Synced: agent_windows/run.py -> $OPT/agent_windows/run.py"
else
    echo "  WARNING: $WORKSPACE/agent_windows/run.py not found"
fi

echo "=== Done ==="
