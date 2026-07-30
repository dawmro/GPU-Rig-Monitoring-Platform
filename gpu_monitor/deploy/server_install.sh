#!/bin/bash
# GPU Rig Monitor Server Deployment Script
# For Ubuntu 22.04 (Jammy) / 24.04 (Noble)
# Run as root
#
# Integrated Celery + Redis migration:
# - Installs Redis, configures it securely
# - Installs Celery stack in Python venv
# - Configures Django settings for Celery
# - Runs migrations for Celery apps
# - Creates systemd units for Celery workers + Beat
# - Starts all services in correct order
# - Creates periodic tasks in Beat
# - Reduces Gunicorn workers (Celery handles ingest)
#
# Design choices:
# - "set -euo pipefail" makes the script fail early on errors
# - Keeps Gunicorn bound to 127.0.0.1:8000 (Nginx is public entry)
# - Nginx is the only public web server on ports 80/443
# - Nginx rate-limit zones defined globally in conf.d
# - Preserves existing .env secrets on rerun
# - Celery workers and Beat managed via systemd

set -euo pipefail

DOMAIN="${1:-monitor.example.com}"
APP_DIR="/opt/gpu_monitor"
APP_USER="monitoring"
DB_NAME="gpu_monitor"
DB_USER="gpu_monitor"

# Detect one local IPv4 address
SERVER_IP="$(hostname -I | awk '{for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+\./) {print $i; exit}}')"

# Build ALLOWED_HOSTS
if [ -n "${SERVER_IP:-}" ]; then
    DJANGO_ALLOWED_HOSTS_VALUE="$DOMAIN,127.0.0.1,localhost,$SERVER_IP"
else
    DJANGO_ALLOWED_HOSTS_VALUE="$DOMAIN,127.0.0.1,localhost"
fi

# Build CSRF_TRUSTED_ORIGINS
if [ -n "${SERVER_IP:-}" ]; then
    CSRF_TRUSTED_ORIGINS_VALUE="https://$DOMAIN,http://$DOMAIN,https://$SERVER_IP,http://$SERVER_IP"
else
    CSRF_TRUSTED_ORIGINS_VALUE="https://$DOMAIN,http://$DOMAIN"
fi

echo "=== GPU Rig Monitor Server Deployment ==="
echo "Domain: $DOMAIN"
echo "Server IP: ${SERVER_IP:-not-detected}"
echo "DJANGO_ALLOWED_HOSTS: $DJANGO_ALLOWED_HOSTS_VALUE"
echo "CSRF_TRUSTED_ORIGINS: $CSRF_TRUSTED_ORIGINS_VALUE"

# ── System packages ───────────────────────────────────────────────────────
echo "==> Installing system packages..."
apt update
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib \
    postgresql-client nginx certbot python3-certbot-nginx \
    ufw git build-essential curl redis-server redis-tools

# Enable PostgreSQL
echo "==> Enabling and starting PostgreSQL..."
systemctl restart postgresql
systemctl enable postgresql

# ── Install and configure Redis ───────────────────────────────────────────
echo "==> Installing and configuring Redis..."
apt install -y redis-server redis-tools

ENV_FILE="/opt/gpu_monitor/.env"

# Generate Redis password if not already in .env
if [ -f "$ENV_FILE" ] && grep -q "^REDIS_PASSWORD=" "$ENV_FILE" 2>/dev/null; then
    echo "==> Existing Redis password found in .env, reusing..."
    REDIS_PASSWORD=$(grep "^REDIS_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)
else
    REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "Generated Redis password (save this!): $REDIS_PASSWORD"
fi

# Configure Redis (secure, memory-bound, no persistence for broker)
REDIS_PASSWORD=$(grep "^REDIS_PASSWORD=" /opt/gpu_monitor/.env 2>/dev/null | cut -d= -f2- || echo "$REDIS_PASSWORD")
sudo tee /etc/redis/redis.conf > /dev/null <<REDIS
bind 127.0.0.1 ::1
requirepass $REDIS_PASSWORD
maxmemory 2gb
maxmemory-policy allkeys-lru
save ""
appendonly no
REDIS

systemctl restart redis-server
systemctl enable redis-server

# Verify Redis
redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG
echo "==> Redis verified successfully"

# ── Secrets loading / generation ──────────────────────────────────────────
APP_DIR="/opt/gpu_monitor"
APP_USER="monitoring"
DB_NAME="gpu_monitor"
DB_USER="gpu_monitor"
ENV_FILE="/opt/gpu_monitor/.env"

EXISTING_DB_PASS=""
EXISTING_DJANGO_SECRET=""
if [ -f "$ENV_FILE" ]; then
    echo "==> Existing .env found, attempting to reuse secrets..."
    EXISTING_DB_PASS=$(grep -E '^DB_PASSWORD=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)
    EXISTING_DJANGO_SECRET=$(grep -E '^DJANGO_SECRET_KEY=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)
fi

DB_PASS="${EXISTING_DB_PASS:-$(python3 -c "import secrets; print(secrets.token_hex(24))")}"
DJANGO_SECRET="${EXISTING_DJANGO_SECRET:-$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")}"

# ── Database setup ────────────────────────────────────────────────────────
echo "==> Configuring PostgreSQL role and database..."
sudo -u postgres psql << EOF
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$DB_USER') THEN
      CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
   ELSE
      ALTER USER $DB_USER WITH PASSWORD '$DB_PASS';
   END IF;
END
\$\$;

SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$DB_NAME')\gexec

GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
EOF

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Database password: $DB_PASS"
echo "  (also saved to $APP_DIR/.env)"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── Application user ──────────────────────────────────────────────────────
if ! id "monitoring" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "monitoring"
    echo "Created user: monitoring"
fi

mkdir -p "/opt/gpu_monitor"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$SCRIPT_DIR" != "/opt/gpu_monitor" ] && [ -f "$SCRIPT_DIR/manage.py" ]; then
    echo "==> Copying project files to /opt/gpu_monitor..."
    cp -r "$SCRIPT_DIR"/* "$APP_DIR/"
    cp -r "$SCRIPT_DIR"/.[!.]* "$APP_DIR/" 2>/dev/null || true
fi

chown -R "monitoring:monitoring" "/opt/gpu_monitor"

# ── Log directories and static directories ────────────────────────────────
mkdir -p "/opt/gpu_monitor/logs"
chown "monitoring:monitoring" "/opt/gpu_monitor/logs"
chmod 755 "/opt/gpu_monitor/logs"

touch "/opt/gpu_monitor/logs/app.log"
touch "/opt/gpu_monitor/logs/gunicorn-access.log"
touch "/opt/gpu_monitor/logs/gunicorn-error.log"
chown "monitoring:monitoring" "/opt/gpu_monitor/logs/app.log"
chown "monitoring:monitoring" "/opt/gpu_monitor/logs/gunicorn-access.log"
chown "monitoring:monitoring" "/opt/gpu_monitor/logs/gunicorn-error.log"
chmod 664 "/opt/gpu_monitor/logs/app.log"
chmod 664 "/opt/gpu_monitor/logs/gunicorn-access.log"
chmod 664 "/opt/gpu_monitor/logs/gunicorn-error.log"

mkdir -p "/opt/gpu_monitor/staticfiles"
chown "monitoring:monitoring" "/opt/gpu_monitor/staticfiles"

# ── Python virtualenv (includes Celery stack) ─────────────────────────────
echo "==> Creating/updating Python virtualenv..."
sudo -u "monitoring" bash << 'APP'
cd /opt/gpu_monitor
if [ ! -d venv ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip

pip install django djangorestframework django-htmx psycopg2-binary argon2-cffi \
    gunicorn requests pyyaml psutil celery redis django-celery-beat django-celery-results
APP

# ── Environment file ──────────────────────────────────────────────────────
# Ensure REDIS_PASSWORD is in .env (generate if missing)
if [ -f "$ENV_FILE" ] && grep -q "^REDIS_PASSWORD=" "$ENV_FILE" 2>/dev/null; then
    REDIS_PASSWORD=$(grep "^REDIS_PASSWORD=" "$ENV_FILE" | cut -d= -f2-)
else
    REDIS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "REDIS_PASSWORD=$REDIS_PASSWORD" >> /opt/gpu_monitor/.env
fi

cat > "/opt/gpu_monitor/.env" << ENVEOF
DJANGO_SECRET_KEY=$DJANGO_SECRET
DJANGO_DEBUG=False

# Django host validation
DJANGO_ALLOWED_HOSTS=$DJANGO_ALLOWED_HOSTS_VALUE

# Django CSRF trusted origins
CSRF_TRUSTED_ORIGINS=$CSRF_TRUSTED_ORIGINS_VALUE

# HTTPS / cookie security
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE=Lax
CSRF_COOKIE_SAMESITE=Lax

# Reverse proxy support
USE_X_FORWARDED_HOST=True
USE_X_FORWARDED_PORT=True

# Extra browser-facing hardening
SECURE_CONTENT_TYPE_NOSNIFF=True
SECURE_BROWSER_XSS_FILTER=True
X_FRAME_OPTIONS=DENY
SECURE_REFERRER_POLICY=same-origin

# HSTS
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False

# Database connection
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=127.0.0.1
DB_PORT=5432

# Redis / Celery
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_PASSWORD=$REDIS_PASSWORD
REDIS_DB_BROKER=0
REDIS_DB_RESULTS=1

CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://:${REDIS_PASSWORD}@127.0.0.1:6379/1

CELERY_TASK_TRACK_STARTED=True
CELERY_TASK_TIME_LIMIT=300
CELERY_TASK_SOFT_TIME_LIMIT=240
CELERY_WORKER_PREFETCH_MULTIPLIER=1
CELERY_WORKER_MAX_TASKS_PER_CHILD=100
CELERY_RESULT_EXPIRES=86400
CELERY_TASK_VISIBILITY_TIMEOUT=3600
CELERY_ACCEPT_CONTENT=['json']
CELERY_TASK_SERIALIZER=json
CELERY_RESULT_SERIALIZER=json
CELERY_TIMEZONE=UTC
CELERY_BEAT_SCHEDULER=django_celery_beat.schedulers.DatabaseScheduler
ENVEOF
chmod 600 "/opt/gpu_monitor/.env"
chown "monitoring:monitoring" "/opt/gpu_monitor/.env"

# ── Django migrations and static files ────────────────────────────────────
echo "==> Running Django migrations..."
sudo -u "monitoring" bash << 'MIGRATE'
cd /opt/gpu_monitor
source venv/bin/activate
set -a
source .env
set +a
python manage.py migrate
python manage.py collectstatic --noinput
MIGRATE

# ── Redis configuration (ensure it's correct) ─────────────────────────────
REDIS_PASSWORD=$(grep '^REDIS_PASSWORD=' /opt/gpu_monitor/.env | cut -d= -f2-)
sudo tee /etc/redis/redis.conf > /dev/null <<REDIS
bind 127.0.0.1 ::1
requirepass $REDIS_PASSWORD
maxmemory 2gb
maxmemory-policy allkeys-lru
save ""
appendonly no
REDIS

systemctl restart redis-server
systemctl enable redis-server

# Verify Redis
redis-cli -a "$REDIS_PASSWORD" ping | grep -q PONG
echo "==> Redis verified successfully"

# ── Celery Django settings ────────────────────────────────────────────────
echo "==> Adding Celery configuration to Django settings..."
SETTINGS_FILE="/opt/gpu_monitor/gpu_monitor/settings.py"
if ! grep -q "CELERY_BROKER_URL" "$SETTINGS_FILE"; then
    cat >> "$SETTINGS_FILE" <<'SETTINGS_EOF'

# Redis / Celery — build URLs from components (same pattern as DB)
REDIS_HOST = os.environ.get('REDIS_HOST', '127.0.0.1')
REDIS_PORT = os.environ.get('REDIS_PORT', '6379')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', '')
REDIS_DB_BROKER = os.environ.get('REDIS_DB_BROKER', '0')
REDIS_DB_RESULTS = os.environ.get('REDIS_DB_RESULTS', '1')

from urllib.parse import quote

def _redis_url(db: str) -> str:
    """Build redis:// URL from components. Handles empty password."""
    auth = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""
    return f"redis://{auth}{REDIS_HOST}:{REDIS_PORT}/{db}"

CELERY_BROKER_URL = _redis_url(REDIS_DB_BROKER)
CELERY_RESULT_BACKEND = _redis_url(REDIS_DB_RESULTS)

# Celery Configuration
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_TASK_SOFT_TIME_LIMIT = 240
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_WORKER_MAX_TASKS_PER_CHILD = 100
CELERY_RESULT_EXPIRES = 86400  # 24h
CELERY_TASK_VISIBILITY_TIMEOUT = 3600  # 1h (covers long compaction)
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'UTC'
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers.DatabaseScheduler'

# Add Celery apps to INSTALLED_APPS
INSTALLED_APPS += [
    'django_celery_beat',
    'django_celery_results',
]
SETTINGS_EOF
fi

# Verify settings load
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python -c "import django; django.setup(); from django.conf import settings; print('CELERY_BROKER_URL:', settings.CELERY_BROKER_URL)"

# ── Django migrations for Celery apps ─────────────────────────────────────
sudo -u monitoring bash << 'MIGRATE'
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
python manage.py migrate django_celery_beat
python manage.py migrate django_celery_results
python manage.py migrate
MIGRATE

# ── Create Celery systemd unit files ──────────────────────────────────────
cat > /etc/systemd/system/celery-ingest@.service <<'EOF'
[Unit]
Description=Celery Ingest Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=ingest \
    --concurrency=2 \
    --pool=prefork \
    --hostname=ingest-worker-%i@%h \
    --max-tasks-per-child=100 \
    --time-limit=300 \
    --soft-time-limit=240
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/celery-maintenance@.service <<'EOF'
[Unit]
Description=Celery Maintenance Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=maintenance \
    --concurrency=1 \
    --pool=prefork \
    --hostname=maint-worker-%i@%h \
    --max-tasks-per-child=10 \
    --time-limit=7200 \
    --soft-time-limit=6600
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/celery-default@.service <<'EOF'
[Unit]
Description=Celery Default Worker %i
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor worker \
    --loglevel=INFO \
    --queues=default,alerts,reports \
    --concurrency=1 \
    --pool=prefork \
    --hostname=default-worker-%i@%h \
    --max-tasks-per-child=50 \
    --time-limit=300 \
    --soft-time-limit=240
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/celery-beat.service <<'EOF'
[Unit]
Description=Celery Beat Scheduler
After=network.target redis.service postgresql.service
Wants=redis.service postgresql.service

[Service]
Type=simple
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/celery -A gpu_monitor beat \
    --loglevel=INFO \
    --scheduler=django_celery_beat.schedulers:DatabaseScheduler \
    --pidfile=/var/run/celery/beat.pid \
    --schedule=/var/lib/celery/beat-schedule
RuntimeDirectory=celery
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# Create runtime directories
mkdir -p /var/run/celery /var/lib/celery
chown monitoring:monitoring /var/run/celery /var/lib/celery

# Verify unit files
systemd-analyze verify /etc/systemd/system/celery-ingest@.service
systemd-analyze verify /etc/systemd/system/celery-maintenance@.service
systemd-analyze verify /etc/systemd/system/celery-default@.service
systemd-analyze verify /etc/systemd/system/celery-beat.service

# Reload systemd
systemctl daemon-reload

# Start Celery services (Beat first, then workers)
echo "==> Starting Celery services..."
systemctl enable --now celery-beat
sleep 3
systemctl status celery-beat --no-pager

systemctl enable --now celery-ingest@1
systemctl enable --now celery-maintenance@1
systemctl enable --now celery-default@1
sleep 2
systemctl status celery-beat celery-ingest@1 celery-maintenance@1 celery-default@1 --no-pager

# Verify Celery connectivity
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
celery -A gpu_monitor inspect ping
celery -A gpu_monitor inspect active_queues

# ── Gunicorn systemd unit (reduced workers since ingest offloaded) ────────
cat > /etc/systemd/system/gunicorn.service << GUNICORN
[Unit]
Description=GPU Rig Monitor - Gunicorn
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=monitoring
Group=monitoring
WorkingDirectory=/opt/gpu_monitor
EnvironmentFile=/opt/gpu_monitor/.env
ExecStart=/opt/gpu_monitor/venv/bin/gunicorn \
    gpu_monitor.wsgi:application \
    --bind 127.0.0.1:8000 \
    --workers 4 \
    --timeout 30 \
    --access-logfile /opt/gpu_monitor/logs/gunicorn-access.log \
    --error-logfile /opt/gpu_monitor/logs/gunicorn-error.log
ExecReload=/bin/kill -s HUP $MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
GUNICORN

systemctl daemon-reload
systemctl restart gunicorn

# ── Nginx global rate-limit zones ────────────────────────────────────────
cat > /etc/nginx/conf.d/gpu_monitor_rate_limits.conf << 'EOF'
limit_req_zone $http_x_rig_uuid zone=rig:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=ip:10m rate=30r/s;
EOF

# ── Nginx site configuration ─────────────────────────────────────────────
DOMAIN="${1:-monitor.example.com}"
cp "/opt/gpu_monitor/deploy/nginx.conf" /etc/nginx/sites-available/gpu_monitor
sed -i "s/monitor.example.com/$DOMAIN/g" /etc/nginx/sites-available/gpu_monitor
ln -sf /etc/nginx/sites-available/gpu_monitor /etc/nginx/sites-enabled/gpu_monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
systemctl enable nginx

# ── TLS certificate with Certbot ─────────────────────────────────────────
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@$DOMAIN" --redirect || {
    echo "⚠️  Certbot failed. Run manually later:"
    echo "   certbot --nginx -d $DOMAIN"
}

# ── Firewall ─────────────────────────────────────────────────────────────
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

# ── Create Celery Beat periodic tasks ────────────────────────────────────
cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a

python -c "
import django
django.setup()
import json
from django_celery_beat.models import PeriodicTask, CrontabSchedule, IntervalSchedule

# 2-minute interval for rig status
schedule_2min, _ = IntervalSchedule.objects.get_or_create(every=2, period=IntervalSchedule.MINUTES)
PeriodicTask.objects.get_or_create(
    name='Update Rig Status (every 2 min)',
    task='rigs.tasks.update_rig_status',
    defaults={'interval': schedule_2min, 'queue': 'maintenance', 'priority': 5, 'enabled': True}
)

# Crontab for 3 AM daily
schedule_3am, _ = CrontabSchedule.objects.get_or_create(minute=0, hour=3, day_of_week='*', day_of_month='*', month_of_year='*')

PeriodicTask.objects.get_or_create(
    name='Compact Data - Tier 2 (3:00 AM)',
    task='metrics_app.tasks.compact_data',
    defaults={'crontab': schedule_3am, 'queue': 'maintenance', 'priority': 3, 'enabled': True, 'kwargs': json.dumps({'phase': 'tier2', 'verbose': True})}
)

schedule_305, _ = CrontabSchedule.objects.get_or_create(minute=5, hour=3, day_of_week='*', day_of_month='*', month_of_year='*')
PeriodicTask.objects.get_or_create(
    name='Compact Data - Tier 3 (3:05 AM)',
    task='metrics_app.tasks.compact_data',
    defaults={'crontab': schedule_305, 'queue': 'maintenance', 'priority': 3, 'enabled': True, 'kwargs': json.dumps({'phase': 'tier3', 'verbose': True})}
)

schedule_310, _ = CrontabSchedule.objects.get_or_create(minute=10, hour=3, day_of_week='*', day_of_month='*', month_of_year='*')
PeriodicTask.objects.get_or_create(
    name='Cleanup Old Data (3:10 AM)',
    task='metrics_app.tasks.cleanup_old_data',
    defaults={'crontab': schedule_310, 'queue': 'maintenance', 'priority': 2, 'enabled': True, 'kwargs': json.dumps({'days': 31, 'verbose': True})}
)

schedule_315, _ = CrontabSchedule.objects.get_or_create(minute=15, hour=3, day_of_week='*', day_of_month='*', month_of_year='*')
PeriodicTask.objects.get_or_create(
    name='VACUUM ANALYZE (3:15 AM)',
    task='metrics_app.tasks.vacuum_analyze',
    defaults={'crontab': schedule_315, 'queue': 'maintenance', 'priority': 1, 'enabled': True, 'kwargs': json.dumps({})}
)

schedule_320, _ = CrontabSchedule.objects.get_or_create(minute=20, hour=3, day_of_week='*', day_of_month='*', month_of_year='*')
PeriodicTask.objects.get_or_create(
    name='Cleanup Audit Log (3:20 AM)',
    task='audit.tasks.cleanup_audit_log',
    defaults={'crontab': schedule_320, 'queue': 'maintenance', 'priority': 4, 'enabled': True, 'kwargs': json.dumps({'days': 90, 'verbose': True})}
)

print('All periodic tasks created/updated')

# Verify Beat is scheduling
import subprocess
result = subprocess.run(['celery', '-A', 'gpu_monitor', 'inspect', 'scheduled'], capture_output=True, text=True)
print(result.stdout)
"

# ── Log Rotation ──────────────────────────────────────────────────────────
cp "/opt/gpu_monitor/deploy/logrotate.conf" /etc/logrotate.d/gpu-monitor

# ── Final service reload and verification ────────────────────────────────
systemctl daemon-reload
systemctl restart gunicorn

sleep 3

# Final verification
systemctl status celery-beat celery-ingest@1 celery-maintenance@1 celery-default@1 gunicorn nginx postgresql redis-server --no-pager

cd /opt/gpu_monitor
source venv/bin/activate
set -a && source .env && set +a
celery -A gpu_monitor inspect ping
celery -A gpu_monitor inspect active_queues

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅  DEPLOYMENT COMPLETE WITH CELERY"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:    https://$DOMAIN/dashboard/rigs/"
echo "  Health:       https://$DOMAIN/api/v1/health/"
echo "  Admin panel:  https://$DOMAIN/admin/"
echo "  Allowed hosts: $DJANGO_ALLOWED_HOSTS_VALUE"
echo "  CSRF origins:  $CSRF_TRUSTED_ORIGINS_VALUE"
echo ""
echo "  Celery workers:"
echo "    celery-ingest@1     (ingest queue, 2 concurrency)"
echo "    celery-maintenance@1 (maintenance queue, 1 concurrency)"
echo "    celery-default@1     (default/alerts/reports, 1 concurrency)"
echo "    celery-beat          (scheduler)"
echo ""
echo "  Create an admin user:"
echo "    sudo -u monitoring bash -c 'cd /opt/gpu_monitor && source venv/bin/activate && source .env && python manage.py createsuperuser'"
echo ""
echo "  Useful commands:"
echo "    systemctl status gunicorn"
echo "    systemctl status celery-beat"
echo "    systemctl status celery-ingest@1"
echo "    journalctl -u celery-ingest@1 -f"
echo "    celery -A gpu_monitor inspect active"
echo "    celery -A gpu_monitor inspect scheduled"
echo "═══════════════════════════════════════════════════════"
echo ""