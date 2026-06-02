#!/bin/bash
# Sync updated files from workspace to /opt/gpu_monitor
# Run this script as root or with sudo

set -e

WORKSPACE="/home/qrv/workspace/GPU-Rig-Monitoring-Platform"
OPT="/opt/gpu_monitor"

echo "=== Syncing updated files ==="

# Models, serializers, views
sudo cp "$WORKSPACE/gpu_monitor/metrics_app/models.py" "$OPT/metrics_app/models.py"
sudo cp "$WORKSPACE/gpu_monitor/metrics_app/serializers.py" "$OPT/metrics_app/serializers.py"
sudo cp "$WORKSPACE/gpu_monitor/metrics_app/views.py" "$OPT/metrics_app/views.py"
sudo cp "$WORKSPACE/gpu_monitor/dashboard/views.py" "$OPT/dashboard/views.py"

# Settings
sudo cp "$WORKSPACE/gpu_monitor/gpu_monitor/settings.py" "$OPT/gpu_monitor/settings.py"

# Migrations
sudo cp "$WORKSPACE/gpu_monitor/metrics_app/migrations/0002_auto_20260602.py" "$OPT/metrics_app/migrations/0002_auto_20260602.py"

# Templates
sudo cp "$WORKSPACE/gpu_monitor/templates/dashboard/rig_detail.html" "$OPT/templates/dashboard/rig_detail.html"
sudo cp "$WORKSPACE/gpu_monitor/templates/dashboard/_rig_name.html" "$OPT/templates/dashboard/_rig_name.html"
sudo cp "$WORKSPACE/gpu_monitor/templates/dashboard/_rig_table.html" "$OPT/templates/dashboard/_rig_table.html"
sudo cp "$WORKSPACE/gpu_monitor/templates/dashboard/_metrics_cards.html" "$OPT/templates/dashboard/_metrics_cards.html"
sudo cp "$WORKSPACE/gpu_monitor/templates/dashboard/rig_list.html" "$OPT/templates/dashboard/rig_list.html"

# Fix permissions
sudo chmod -R 644 "$OPT/templates/dashboard/"
sudo chmod -R 755 "$OPT/templates/dashboard/"
sudo chmod 644 "$OPT/metrics_app/models.py" "$OPT/metrics_app/serializers.py" "$OPT/metrics_app/views.py"
sudo chmod 644 "$OPT/metrics_app/migrations/0002_auto_20260602.py"
sudo chmod 644 "$OPT/dashboard/views.py"

echo "=== Running migrations ==="
cd "$OPT"
source venv/bin/activate
set -a && source .env && set +a
python manage.py migrate

echo "=== Restarting Gunicorn ==="
sudo systemctl restart gunicorn

echo "=== Verifying ==="
curl -s http://localhost/api/v1/health/ | python3 -m json.tool

echo "=== Done ==="
