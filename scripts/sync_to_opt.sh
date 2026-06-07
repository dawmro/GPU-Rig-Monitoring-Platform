#!/bin/bash
# Sync GPU Rig Monitoring Platform from workspace to /opt
# Safe to run repeatedly — never touches .env, venv, logs, or user data
#
# Usage:
#   bash scripts/sync_to_opt.sh              # full sync (default)
#   bash scripts/sync_to_opt.sh --no-migrate # skip makemigrations (faster)
#   sudo bash scripts/sync_to_opt.sh         # if file ownership needs root

WORKSPACE="/home/qrv/workspace/GPU-Rig-Monitoring-Platform"
OPT="/opt"

set -e
echo "=== Syncing $WORKSPACE -> $OPT ==="

# ── Step 1: Copy Django source code ─────────────────────────────────
# Copy BEFORE generating migrations so makemigrations sees the latest models.
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
        rsync -av --delete \
            --exclude='__pycache__' \
            --exclude='*.pyc' \
            "$SRC/" "$DST/" 2>/dev/null \
        || cp -r "$SRC"/* "$DST/" 2>/dev/null || true
        echo "  Synced: $app"
    fi
done

# ── Step 2: Copy templates ──────────────────────────────────────────
echo "--- Templates ---"
rsync -av --delete \
    --exclude='__pycache__' \
    "$WORKSPACE/gpu_monitor/templates/" "$OPT/gpu_monitor/templates/" 2>/dev/null \
|| cp -r "$WORKSPACE/gpu_monitor/templates/"* "$OPT/gpu_monitor/templates/" 2>/dev/null || true

# ── Step 3: Copy management commands ────────────────────────────────
echo "--- Management commands ---"
for cmd_dir in "$WORKSPACE/gpu_monitor/"*/management/commands/; do
    [ -d "$cmd_dir" ] || continue
    rel="${cmd_dir#$WORKSPACE/gpu_monitor/}"
    mkdir -p "$OPT/gpu_monitor/$rel"
    cp "$cmd_dir"*.py "$OPT/gpu_monitor/$rel/" 2>/dev/null || true
done

# ── Step 4: Copy agent (Linux) ──────────────────────────────────────
echo "--- Agent (Linux) ---"
if [ -f "$WORKSPACE/agent/run.py" ]; then
    sudo cp "$WORKSPACE/agent/run.py" "$OPT/monitoring-agent/run.py"
    sudo chmod +x "$OPT/monitoring-agent/run.py"
    echo "  Synced: agent/run.py"
fi

# ── Step 5: Copy agent (Windows) ────────────────────────────────────
echo "--- Agent (Windows) ---"
if [ -f "$WORKSPACE/agent_windows/run.py" ]; then
    sudo mkdir -p "$OPT/agent_windows"
    sudo cp "$WORKSPACE/agent_windows/run.py" "$OPT/agent_windows/run.py"
    sudo chmod +x "$OPT/agent_windows/run.py"
    echo "  Synced: agent_windows/run.py"
fi

# ── Step 6: Copy scripts (new ones only) ────────────────────────────
echo "--- Scripts ---"
mkdir -p "$OPT/gpu_monitor/deploy"
for script in "$WORKSPACE/scripts/"*.sh; do
    [ -f "$script" ] || continue
    base=$(basename "$script")
    if [ ! -f "$OPT/gpu_monitor/deploy/$base" ]; then
        cp "$script" "$OPT/gpu_monitor/deploy/$base"
        chmod +x "$OPT/gpu_monitor/deploy/$base"
        echo "  New script: $base"
    else
        echo "  Skipped (exists): $base"
    fi
done

# ── Step 7: Fix permissions ────────────────────────────────────────
echo "--- Fixing permissions ---"
find "$OPT/gpu_monitor" -name "*.py" -exec chmod 644 {} \;
find "$OPT/gpu_monitor/templates" -name "*.html" -exec chmod 644 {} \;
find "$OPT/gpu_monitor" -type d -exec chmod 755 {} \;
find "$OPT/gpu_monitor/deploy" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true
find "$OPT/gpu_monitor" -name "manage.py" -exec chmod 755 {} \; 2>/dev/null || true

# ── Step 8: Generate + apply migrations in /opt ─────────────────────
# Source code is already synced, so makemigrations sees the latest models.
# After generating, copy new migration files BACK to workspace for git tracking.
cd "$OPT/gpu_monitor"
source venv/bin/activate
set -a && source .env && set +a

if [[ "$1" != "--no-migrate" ]]; then
    echo "--- Checking for model changes ---"
    if python manage.py makemigrations --check 2>/dev/null; then
        echo "  No model changes — migrations up to date"
    else
        echo "  Model changes detected — creating migrations..."
        python manage.py makemigrations

        # Copy new migration files back to workspace for git tracking
        echo "  Copying new migrations back to workspace..."
        for mig_dir in "$OPT/gpu_monitor/"*/migrations/; do
            [ -d "$mig_dir" ] || continue
            rel="${mig_dir#$OPT/gpu_monitor/}"
            ws_dir="$WORKSPACE/gpu_monitor/$rel"
            mkdir -p "$ws_dir"
            for f in "$mig_dir"00*.py; do
                [ -f "$f" ] || continue
                base=$(basename "$f")
                if [ ! -f "$ws_dir/$base" ]; then
                    cp "$f" "$ws_dir/$base"
                    echo "    -> workspace: $rel/$base"
                fi
            done
        done
    fi
else
    echo "--- Skipping makemigrations (--no-migrate)"
fi

# Apply migrations
echo "--- Applying migrations ---"
if python manage.py migrate --check 2>/dev/null; then
    echo "  No new migrations to apply"
else
    echo "  Applying..."
    python manage.py migrate
    echo "  Collecting static files..."
    python manage.py collectstatic --noinput
fi

# ── Step 9: Restart Gunicorn ───────────────────────────────────────
echo "--- Restarting Gunicorn ---"
sudo systemctl restart gunicorn
sleep 2
sudo systemctl status gunicorn --no-pager

sleep 2
echo "=== Done ==="
curl -s http://localhost/api/v1/health | python3 -m json.tool

# ── NEVER OVERWRITTEN ───────────────────────────────────────────────
# .env, venv/, logs/, staticfiles/, config.yaml, cron jobs, systemd units,
# and the PostgreSQL database are never touched by this script.
