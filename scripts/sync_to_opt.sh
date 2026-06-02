#!/bin/bash
# Sync GPU Rig Monitoring Platform from workspace to /opt
# Safe to run repeatedly — never touches database, .env, venv, logs, or user data

WORKSPACE="/home/qrv/workspace/GPU-Rig-Monitoring-Platform"
OPT="/opt"

set -e
echo "=== Syncing $WORKSPACE -> $OPT ==="

# ── Django source code (always sync) ──────────────────────────────────
echo "--- Django apps ---"

APPS=(
    "gpu_monitor/gpu_monitor"
    "gpu_monitor/accounts"
    "gpu_monitor/rigs"
    "gpu_monitor/metrics_app"
    "gpu_monitor/dashboard"
    "gpu_monitor/audit"
)

for app in "${APPS[@]}"; do
    SRC="$WORKSPACE/$app"
    DST="$OPT/$app"
    if [ -d "$SRC" ]; then
        # Copy everything except __pycache__ and *.pyc
        rsync -av --delete \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            "$SRC/" "$DST/" 2>/dev/null \
        || cp -r "$SRC"/* "$DST/" 2>/dev/null || true
        echo "  Synced: $app"
    fi
done

# ── Templates (always sync) ───────────────────────────────────────────
echo "--- Templates ---"
rsync -av --delete \
    --exclude='__pycache__' \
    "$WORKSPACE/gpu_monitor/templates/" "$OPT/gpu_monitor/templates/" 2>/dev/null \
|| cp -r "$WORKSPACE/gpu_monitor/templates/"* "$OPT/gpu_monitor/templates/" 2>/dev/null || true

# ── Management commands (always sync) ────────────────────────────────
echo "--- Management commands ---"
for cmd_dir in "$WORKSPACE/gpu_monitor/"*/management/commands/; do
    [ -d "$cmd_dir" ] || continue
    rel="${cmd_dir#$WORKSPACE/gpu_monitor/}"
    mkdir -p "$OPT/gpu_monitor/$rel"
    cp "$cmd_dir"*.py "$OPT/gpu_monitor/$rel/" 2>/dev/null || true
done

# ── Agent (Linux) ────────────────────────────────────────────────────
echo "--- Agent (Linux) ---"
if [ -f "$WORKSPACE/agent/run.py" ]; then
    cp "$WORKSPACE/agent/run.py" "$OPT/monitoring-agent/run.py"
    chmod +x "$OPT/monitoring-agent/run.py"
    echo "  Synced: agent/run.py"
fi

# ── Agent (Windows) ──────────────────────────────────────────────────
echo "--- Agent (Windows) ---"
if [ -f "$WORKSPACE/agent_windows/run.py" ]; then
    mkdir -p "$OPT/agent_windows"
    cp "$WORKSPACE/agent_windows/run.py" "$OPT/agent_windows/run.py"
    chmod +x "$OPT/agent_windows/run.py"
    echo "  Synced: agent_windows/run.py"
fi

# ── Scripts (deploy helpers, cron wrappers) ──────────────────────────
echo "--- Scripts ---"
mkdir -p "$OPT/gpu_monitor/deploy"
for script in "$WORKSPACE/scripts/"*.sh; do
    [ -f "$script" ] || continue
    base=$(basename "$script")
    # Don't overwrite update_rig_status.sh if it already exists in opt
    # (it may have been customized)
    if [ ! -f "$OPT/gpu_monitor/deploy/$base" ]; then
        cp "$script" "$OPT/gpu_monitor/deploy/$base"
        chmod +x "$OPT/gpu_monitor/deploy/$base"
        echo "  New script: $base"
    else
        echo "  Skipped (exists): $base"
    fi
done

# ── Migrations (only copy NEW files, never delete) ───────────────────
echo "--- Migrations ---"
for mig_dir in "$WORKSPACE/gpu_monitor/"*/migrations/; do
    [ -d "$mig_dir" ] || continue
    rel="${mig_dir#$WORKSPACE/gpu_monitor/}"
    mkdir -p "$OPT/gpu_monitor/$rel"
    for f in "$mig_dir"00*.py; do
        [ -f "$f" ] || continue
        base=$(basename "$f")
        if [ ! -f "$OPT/gpu_monitor/$rel/$base" ]; then
            cp "$f" "$OPT/gpu_monitor/$rel/$base"
            echo "  New migration: $rel/$base"
        fi
    done
    # Also copy __init__.py if missing
    if [ ! -f "$OPT/gpu_monitor/$rel/__init__.py" ]; then
        cp "$mig_dir/__init__.py" "$OPT/gpu_monitor/$rel/__init__.py" 2>/dev/null || true
    fi
done

# ── Fix permissions (Gunicorn must read all files) ───────────────────
echo "--- Fixing permissions ---"
# All .py files: readable by all, writable by owner
find "$OPT/gpu_monitor" -name "*.py" -exec chmod 644 {} \;
# All .html templates: readable by all, writable by owner
find "$OPT/gpu_monitor/templates" -name "*.html" -exec chmod 644 {} \;
# Directories: executable (=listable) by all
find "$OPT/gpu_monitor" -type d -exec chmod 755 {} \;
# Scripts: executable
find "$OPT/gpu_monitor/deploy" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true
find "$OPT/gpu_monitor" -name "manage.py" -exec chmod 755 {} \; 2>/dev/null || true

# ── Run migrations if any new ones were copied ──────────────────────
echo "--- Checking migrations ---"
cd "$OPT/gpu_monitor"
source venv/bin/activate
set -a && source .env && set +a
python manage.py migrate --check 2>/dev/null && echo "  No new migrations" || {
    echo "  Running migrations..."
    python manage.py migrate
    python manage.py collectstatic --noinput
}

# ── Restart Gunicorn ─────────────────────────────────────────────────
echo "--- Restarting Gunicorn ---"
pkill -f "gunicorn.*gpu_monitor" 2>/dev/null || true
sleep 2
gunicorn gpu_monitor.wsgi:application --bind 127.0.0.1:8000 --workers 4 --timeout 30 &

sleep 2
echo "=== Done ==="
curl -s http://localhost/api/v1/health/ | python3 -m json.tool

# ── DO NOT OVERWRITE (documented for reference) ─────────────────────
# The following are NEVER touched by this script:
#
# /opt/gpu_monitor/.env         — secrets, database credentials (edit manually)
# /opt/gpu_monitor/venv/         — Python virtual environment (never overwrite)
# /opt/gpu_monitor/logs/         — application logs (never delete)
# /opt/gpu_monitor/staticfiles/  — collected static files (regen via collectstatic)
# /opt/monitoring-agent/config.yaml — per-agent credentials (edit manually)
# /etc/cron.d/*                  — cron jobs (set up once, edit manually)
# /etc/systemd/system/gunicorn.service — systemd unit (set up once)
# Database (PostgreSQL)          — never touched by file copy
