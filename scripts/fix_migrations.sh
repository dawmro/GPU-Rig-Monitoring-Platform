#!/bin/bash
# Fix for migration issue on production server
# Run this on the production server as the qrv user

cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

echo "=== Checking current migration state ==="
python manage.py showmigrations metrics_app | grep -E "002[45]"

echo ""
echo "=== Deleting stale auto-generated migrations ==="
# Remove any auto-generated migrations that conflict with our manual ones
for f in gpu_monitor/metrics_app/migrations/0025_alter_dockercontainermetric*.py; do
    if [ -f "$f" ]; then
        echo "  Removing: $f"
        rm -f "$f"
    fi
done

echo ""
echo "=== Unapplying metrics_app migration 0024 if it failed mid-way ==="
python manage.py migrate metrics_app 0023 --fake 2>/dev/null || true

echo ""
echo "=== Applying migrations ==="
python manage.py migrate metrics_app 0024
python manage.py migrate metrics_app 0025
python manage.py migrate metrics_app 0026
python manage.py migrate  # Apply any remaining

echo ""
echo "=== Restarting Gunicorn ==="
sudo systemctl restart gunicorn

echo ""
echo "=== Done ==="
