#!/bin/bash
# GPU Rig Monitor Server Deployment Script
# For Ubuntu 22.04 (Jammy) / 24.04 (Noble)
# Run as root

set -euo pipefail

DOMAIN="${1:-monitor.example.com}"
APP_DIR="/opt/gpu_monitor"
APP_USER="monitoring"
DB_NAME="gpu_monitor"
DB_USER="gpu_monitor"

echo "=== GPU Rig Monitor Server Deployment ==="
echo "Domain: $DOMAIN"

# ── System packages ───────────────────────────────────────────────────────
apt update
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib \
    postgresql-client nginx certbot python3-certbot-nginx \
    ufw git build-essential curl

# Enable and start PostgreSQL
systemctl restart postgresql
systemctl enable postgresql

# ── Database setup ────────────────────────────────────────────────────────
DB_PASS=$(python3 -c "import secrets; print(secrets.token_hex(24))")

sudo -u postgres psql << EOF
CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
CREATE DATABASE $DB_NAME OWNER $DB_USER;
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
\c $DB_NAME
EOF

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  Database password: $DB_PASS"
echo "  (also saved to $APP_DIR/.env)"
echo "═══════════════════════════════════════════════════════"
echo ""

# ── Application user ───────────────────────────────────────────────────────
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
    echo "Created user: $APP_USER"
fi

# ── Copy project files ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$SCRIPT_DIR" != "$APP_DIR" ] && [ -f "$SCRIPT_DIR/manage.py" ]; then
    echo "==> Copying project files to $APP_DIR..."
    cp -r "$SCRIPT_DIR"/* "$APP_DIR/"
    cp -r "$SCRIPT_DIR"/.[!.]* "$APP_DIR/" 2>/dev/null || true
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ── Log directory and files ──────────────────────────────────────────────────
mkdir -p "$APP_DIR/logs"
chown "$APP_USER:$APP_USER" "$APP_DIR/logs"
chmod 755 "$APP_DIR/logs"

# Create log files that Django and Gunicorn expect (prevents PermissionError)
touch "$APP_DIR/logs/app.log"
touch "$APP_DIR/logs/gunicorn-access.log"
touch "$APP_DIR/logs/gunicorn-error.log"
chown "$APP_USER:$APP_USER" "$APP_DIR/logs/app.log"
chown "$APP_USER:$APP_USER" "$APP_DIR/logs/gunicorn-access.log"
chown "$APP_USER:$APP_USER" "$APP_DIR/logs/gunicorn-error.log"
chmod 664 "$APP_DIR/logs/app.log"
chmod 664 "$APP_DIR/logs/gunicorn-access.log"
chmod 664 "$APP_DIR/logs/gunicorn-error.log"

mkdir -p "$APP_DIR/staticfiles"
chown "$APP_USER:$APP_USER" "$APP_DIR/staticfiles"

# ── Python virtualenv ──────────────────────────────────────────────────────
sudo -u "$APP_USER" bash << 'APP'
cd /opt/gpu_monitor
if [ ! -d venv ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip
pip install django djangorestframework django-htmx psycopg2-binary argon2-cffi \
    gunicorn requests pyyaml psutil
APP

# ── Environment file ──────────────────────────────────────────────────────
DJANGO_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")

cat > "$APP_DIR/.env" << ENVEOF
DJANGO_SECRET_KEY=$DJANGO_SECRET
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=$DOMAIN
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=127.0.0.1
DB_PORT=5432
ENVEOF
chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

# ── Migrations ─────────────────────────────────────────────────────────────
echo "==> Running Django migrations..."
sudo -u "$APP_USER" bash << 'MIGRATE'
cd /opt/gpu_monitor
source venv/bin/activate
export $(grep -v '^#' .env | xargs -d '\n')
python manage.py migrate
python manage.py collectstatic --noinput
MIGRATE

# ── Gunicorn systemd ──────────────────────────────────────────────────────
GUNICORN_WORKERS=$(( $(nproc) * 2 + 1 ))
[ "$GUNICORN_WORKERS" -gt 8 ] && GUNICORN_WORKERS=8

cat > /etc/systemd/system/gunicorn.service << GUNICORN
[Unit]
Description=GPU Rig Monitor - Gunicorn
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn \\
    gpu_monitor.wsgi:application \\
    --bind 127.0.0.1:8000 \\
    --workers $GUNICORN_WORKERS \\
    --timeout 30 \\
    --access-logfile $APP_DIR/logs/gunicorn-access.log \\
    --error-logfile $APP_DIR/logs/gunicorn-error.log
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
GUNICORN

# ── Nginx ─────────────────────────────────────────────────────────────────
echo "==> Configuring Nginx..."
cp /opt/gpu_monitor/deploy/nginx.conf /etc/nginx/sites-available/gpu_monitor
sed -i "s/monitor.example.com/$DOMAIN/g" /etc/nginx/sites-available/gpu_monitor
ln -sf /etc/nginx/sites-available/gpu_monitor /etc/nginx/sites-enabled/gpu_monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx
systemctl enable nginx

# ── TLS certificate ────────────────────────────────────────────────────────
echo "==> Obtaining TLS certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@$DOMAIN" --redirect || {
    echo "⚠️  Certbot failed. Run manually later:"
    echo "   certbot --nginx -d $DOMAIN"
}

# ── Firewall ───────────────────────────────────────────────────────────────
echo "==> Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

# ── Start services ─────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable gunicorn
systemctl start gunicorn
systemctl enable postgresql nginx

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅  DEPLOYMENT COMPLETE"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:    https://$DOMAIN/dashboard/rigs/"
echo "  Health:       https://$DOMAIN/api/v1/health/"
echo "  Admin panel:  https://$DOMAIN/admin/"
echo ""
echo "  Create an admin user:"
echo "    sudo -u $APP_USER bash -c 'cd $APP_DIR && source venv/bin/activate && set -a && source .env && set +a && python manage.py createsuperuser'"
echo ""
echo "  Useful commands:"
echo "    systemctl status gunicorn"
echo "    systemctl status postgresql"
echo "    tail -f $APP_DIR/logs/gunicorn-error.log"
echo "═══════════════════════════════════════════════════════"
