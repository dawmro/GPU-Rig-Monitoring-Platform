#!/bin/bash
# Rig status update wrapper — called by cron every 2 minutes
cd /opt/gpu_monitor
. venv/bin/activate
python manage.py update_rig_status >> /opt/gpu_monitor/logs/rig_status.log 2>&1
