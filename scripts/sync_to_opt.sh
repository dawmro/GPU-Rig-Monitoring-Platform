#!/bin/bash
# Sync GPU Rig Monitoring Platform from workspace to /opt
# Safe to run repeatedly — never touches .env, venv, logs, or user data
#
# Usage:
#   bash scripts/sync_to_opt.sh                    # full sync (default)
#   bash scripts/sync_to_opt.sh --no-migrate       # skip makemigrations (faster)
#   sudo bash scripts/sync_to_opt.sh [USER]        # if file ownership needs root
#                                                   # USER defaults to SUDO_USER or LOGNAME

OPT="/opt"

# Parse options
NO_MIGRATE=false
SYNC_USER=""

for arg in "$@"; do
    case "$arg" in
        --no-migrate)
            NO_MIGRATE=true
            ;;
        --help|-h)
            sed -n '2,10p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        -*)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
        *)
            # Positional argument = username
            SYNC_USER="$arg"
            ;;
    esac
done

# Determine workspace user: argument > SUDO_USER > LOGNAME > current user
if [ -z "$SYNC_USER" ]; then
    SYNC_USER="${SUDO_USER:-${LOGNAME:-$(whoami)}}"
fi

WORKSPACE="/home/$SYNC_USER/workspace/GPU-Rig-Monitoring-Platform"

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
# Sync check_update.py (auto-update checker)
if [ -f "$WORKSPACE/agent/check_update.py" ]; then
    sudo cp "$WORKSPACE/agent/check_update.py" "$OPT/monitoring-agent/check_update.py"
    sudo chmod +x "$OPT/monitoring-agent/check_update.py"
    echo "  Synced: agent/check_update.py"
fi
# Sync install.sh
if [ -f "$WORKSPACE/agent/install.sh" ]; then
    sudo cp "$WORKSPACE/agent/install.sh" "$OPT/monitoring-agent/install.sh"
    sudo chmod +x "$OPT/monitoring-agent/install.sh"
    echo "  Synced: agent/install.sh"
fi

# ── Step 5: Copy agent (Windows) ────────────────────────────────────
echo "--- Agent (Windows) ---"
if [ -f "$WORKSPACE/agent_windows/run.py" ]; then
    sudo mkdir -p "$OPT/agent_windows"
    sudo cp "$WORKSPACE/agent_windows/run.py" "$OPT/agent_windows/run.py"
    sudo chmod +x "$OPT/agent_windows/run.py"
    echo "  Synced: agent_windows/run.py"
fi
# Sync check_update.py (Windows)
if [ -f "$WORKSPACE/agent_windows/check_update.py" ]; then
    sudo cp "$WORKSPACE/agent_windows/check_update.py" "$OPT/agent_windows/check_update.py"
    sudo chmod +x "$OPT/agent_windows/check_update.py"
    echo "  Synced: agent_windows/check_update.py"
fi

# ── Step 6: Copy deploy scripts (always sync — content may change) ─────
echo "--- Deploy scripts ---"
mkdir -p "$OPT/gpu_monitor/deploy"
for script in "$WORKSPACE/gpu_monitor/deploy/"*.sh; do
    [ -f "$script" ] || continue
    base=$(basename "$script")
    cp "$script" "$OPT/gpu_monitor/deploy/$base"
    chmod +x "$OPT/gpu_monitor/deploy/$base"
    echo "  Synced: $base"
done
# Also copy top-level scripts
for script in "$WORKSPACE/scripts/"*.sh; do
    [ -f "$script" ] || continue
    base=$(basename "$script")
    cp "$script" "$OPT/gpu_monitor/deploy/$base"
    chmod +x "$OPT/gpu_monitor/deploy/$base"
    echo "  Synced: $base"
done

# ── Step 7: Fix permissions ────────────────────────────────────────
echo "--- Fixing permissions ---"

# Server-side: Django files owned by deploy/monitoring user
# (whoever runs the server)
find "$OPT/gpu_monitor" -name "*.py" -exec chmod 644 {} \;
find "$OPT/gpu_monitor/templates" -name "*.html" -exec chmod 644 {} \;
find "$OPT/gpu_monitor" -type d -exec chmod 755 {} \;
find "$OPT/gpu_monitor/deploy" -name "*.sh" -exec chmod 755 {} \; 2>/dev/null || true
find "$OPT/gpu_monitor" -name "manage.py" -exec chmod 755 {} \; 2>/dev/null || true

# Agent-side: all files must be readable/executable by monitoring-agent
if [ -d "$OPT/monitoring-agent" ]; then
    chown -R monitoring-agent:monitoring-agent "$OPT/monitoring-agent"
    chmod 755 "$OPT/monitoring-agent"
    [ -f "$OPT/monitoring-agent/run.py" ] && chmod 755 "$OPT/monitoring-agent/run.py"
    [ -f "$OPT/monitoring-agent/check_update.py" ] && chmod 755 "$OPT/monitoring-agent/check_update.py"
    [ -f "$OPT/monitoring-agent/install.sh" ] && chmod 755 "$OPT/monitoring-agent/install.sh"
fi

# Agent log directory must be writable by monitoring-agent
if [ -d "/var/log/monitoring-agent" ]; then
    chown -R monitoring-agent:monitoring-agent /var/log/monitoring-agent/
    chmod 755 /var/log/monitoring-agent/
fi

# Clear stale .pyc cache (root-owned from gunicorn) to prevent migration loader issues
find "$OPT/gpu_monitor" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# ── Step 8: Generate + apply migrations in /opt ─────────────────────
# Source code is already synced, so makemigrations sees the latest models.
# After generating, copy new migration files BACK to workspace for git tracking.
cd "$OPT/gpu_monitor"
source venv/bin/activate
# Source .env safely — export only KEY=VALUE lines, skip comments and empty lines
while IFS='=' read -r key value; do
    # Skip comments and empty lines
    [[ "$key" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$key" ]] && continue
    # Remove leading/trailing whitespace from key
    key=$(echo "$key" | xargs)
    # Export the variable
    export "$key=$value"
done < .env

if [[ "$NO_MIGRATE" == "false" ]]; then
    echo "--- Checking for model changes ---"
    if python manage.py makemigrations --check 2>/dev/null; then
        echo "  No model changes — migrations up to date"
    else
        echo "  Model changes detected — creating migrations..."
        python manage.py makemigrations

        # Remove auto-generated ErrorEventOccurrence migrations — table was
        # manually dropped; keeping the model out of models.py prevents Django
        # from trying to manage it.
        for mig_dir in "$OPT/gpu_monitor/"*/migrations/; do
            [ -d "$mig_dir" ] || continue
            for f in "$mig_dir"*_erroreventoccurrence*.py; do
                [ -f "$f" ] || continue
                echo "  Removing stale migration: $(basename "$f")"
                rm -f "$f"
            done
        done

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
