#!/bin/bash
# GPU Rig Monitor Server Deployment Script
# For Ubuntu 22.04 (Jammy) / 24.04 (Noble)
# Run as root
#
# Why this script exists:
# - install system packages needed by Django, PostgreSQL, Nginx, Gunicorn, Certbot, and firewall rules
# - prepare a dedicated application user instead of running the app as root
# - create or update the PostgreSQL role/database in a rerunnable way
# - create a Python virtual environment for isolated Python dependencies
# - write a production .env file with Django/database settings
# - run migrations and collect static assets
# - configure systemd so Gunicorn starts on boot and can be managed like a normal service
# - configure Nginx as the public reverse proxy in front of Gunicorn
# - request a TLS certificate with Certbot
# - apply basic firewall policy for SSH/HTTP/HTTPS only
#
# Design choices:
# - "set -euo pipefail" makes the script fail early on errors, missing vars, and pipe failures.
# - We keep Gunicorn bound to 127.0.0.1:8000 so it is not exposed directly to the internet.
# - We let Nginx be the only public web server on ports 80/443.
# - We define Nginx rate-limit zones globally in conf.d because limit_req_zone is only valid
#   in the http context, not inside a server/location block.
# - We preserve existing .env secrets on rerun when possible, because rotating Django SECRET_KEY
#   on every deploy would invalidate sessions and signed data.

set -euo pipefail

DOMAIN="${1:-monitor.example.com}"
APP_DIR="/opt/gpu_monitor"
APP_USER="monitoring"
DB_NAME="gpu_monitor"
DB_USER="gpu_monitor"

# Detect one local IPv4 address from the server.
# Why: Django ALLOWED_HOSTS should explicitly allow the domain plus local access methods
# commonly used during health checks, reverse-proxy tests, or temporary IP-based access.
# "hostname -I" returns the host's IP addresses; we pick the first IPv4-like token.
SERVER_IP="$(hostname -I | awk '{for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+\./) {print $i; exit}}')"

# Build a comma-separated ALLOWED_HOSTS value that matches your settings.py parsing:
# ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')
# Why include these:
# - $DOMAIN: the real public hostname users visit
# - 127.0.0.1 and localhost: useful for local checks from the server itself
# - $SERVER_IP: useful if you temporarily access the site directly by server IP
if [ -n "${SERVER_IP:-}" ]; then
    DJANGO_ALLOWED_HOSTS_VALUE="$DOMAIN,127.0.0.1,localhost,$SERVER_IP"
else
    DJANGO_ALLOWED_HOSTS_VALUE="$DOMAIN,127.0.0.1,localhost"
fi

echo "=== GPU Rig Monitor Server Deployment ==="
echo "Domain: $DOMAIN"
echo "Server IP: ${SERVER_IP:-not-detected}"
echo "DJANGO_ALLOWED_HOSTS: $DJANGO_ALLOWED_HOSTS_VALUE"

# ── System packages ───────────────────────────────────────────────────────
# Install everything the stack needs:
# - python3/venv/pip: Django runtime and virtualenv support
# - postgresql packages: database server and client tools
# - nginx: public reverse proxy
# - certbot + python3-certbot-nginx: Let's Encrypt certificates using Nginx integration
# - ufw: simple firewall management
# - git/build-essential/curl: common deployment tools and troubleshooting helpers
echo "==> Installing system packages..."
apt update
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib \
    postgresql-client nginx certbot python3-certbot-nginx \
    ufw git build-essential curl

# Ensure PostgreSQL is running now and also starts automatically after reboot.
echo "==> Enabling and starting PostgreSQL..."
systemctl restart postgresql
systemctl enable postgresql

# ── Secrets loading / generation ──────────────────────────────────────────
# Preserve existing secrets on rerun whenever .env already exists.
# Why:
# - changing Django SECRET_KEY on every run logs everyone out and invalidates signed values
# - changing DB password every run is okay only if we also update the app config, but preserving
#   stable credentials is usually easier operationally
EXISTING_DB_PASS=""
EXISTING_DJANGO_SECRET=""

if [ -f "$APP_DIR/.env" ]; then
    echo "==> Existing .env found, attempting to reuse secrets..."
    EXISTING_DB_PASS="$(grep -E '^DB_PASSWORD=' "$APP_DIR/.env" | head -n1 | cut -d= -f2- || true)"
    EXISTING_DJANGO_SECRET="$(grep -E '^DJANGO_SECRET_KEY=' "$APP_DIR/.env" | head -n1 | cut -d= -f2- || true)"
fi

DB_PASS="${EXISTING_DB_PASS:-$(python3 -c "import secrets; print(secrets.token_hex(24))")}"
DJANGO_SECRET="${EXISTING_DJANGO_SECRET:-$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")}"

# ── Database setup ────────────────────────────────────────────────────────
# Create or update the PostgreSQL user and create the database if missing.
# Why this way:
# - CREATE USER fails if the role already exists, so we wrap it in a DO block
# - ALTER USER updates the password on rerun if needed
# - CREATE DATABASE is done only when missing
# - GRANT ALL PRIVILEGES is safe to run repeatedly
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
# Run the app as a dedicated low-privilege system user instead of root.
# Why:
# - reduces blast radius if the app is compromised
# - gives cleaner file ownership for logs, venv, static files, etc.
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
    echo "Created user: $APP_USER"
fi

# Ensure application directory exists before copying files.
mkdir -p "$APP_DIR"

# ── Copy project files ────────────────────────────────────────────────────
# Copy the project into /opt/gpu_monitor when the script is being run from the repo tree.
# Why:
# - keeps deployed app in a standard fixed path used by Gunicorn, Nginx, and systemd
# - avoids depending on the current working directory during service startup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$SCRIPT_DIR" != "$APP_DIR" ] && [ -f "$SCRIPT_DIR/manage.py" ]; then
    echo "==> Copying project files to $APP_DIR..."
    cp -r "$SCRIPT_DIR"/* "$APP_DIR/"
    cp -r "$SCRIPT_DIR"/.[!.]* "$APP_DIR/" 2>/dev/null || true
fi

# Make sure the app user owns the deployment tree.
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ── Log directories and static directories ────────────────────────────────
# Pre-create directories and files that Django/Gunicorn expect.
# Why:
# - prevents PermissionError at runtime
# - makes log file locations predictable for support/debugging
echo "==> Preparing logs and static directories..."
mkdir -p "$APP_DIR/logs"
chown "$APP_USER:$APP_USER" "$APP_DIR/logs"
chmod 755 "$APP_DIR/logs"

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

# ── Python virtualenv ─────────────────────────────────────────────────────
# Create a venv once and keep reinstalling/updating Python packages inside it.
# Why:
# - isolates app packages from the system Python
# - allows reruns without rebuilding everything from scratch
echo "==> Creating/updating Python virtualenv..."
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
# Write the app runtime configuration.
# Why:
# - keeps secrets and deployment-specific values out of source code
# - systemd can import these vars directly with EnvironmentFile=
# - Django settings.py already splits DJANGO_ALLOWED_HOSTS on commas
echo "==> Writing application .env..."
cat > "$APP_DIR/.env" << ENVEOF
DJANGO_SECRET_KEY=$DJANGO_SECRET
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=$DJANGO_ALLOWED_HOSTS_VALUE
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=127.0.0.1
DB_PORT=5432
ENVEOF
chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

# ── Django migrations and static files ────────────────────────────────────
# Load .env into the shell and apply schema changes before the service starts.
# Why:
# - ensures the database schema matches the code
# - collects static assets to STATIC_ROOT for Nginx or Django to serve consistently
echo "==> Running Django migrations..."
sudo -u "$APP_USER" bash << 'MIGRATE'
cd /opt/gpu_monitor
source venv/bin/activate
set -a
source .env
set +a
python manage.py migrate
python manage.py collectstatic --noinput
MIGRATE

# ── Gunicorn systemd unit ────────────────────────────────────────────────
# Systemd is the process manager for Gunicorn.
# Why:
# - automatic startup on boot
# - restart on crash
# - standard service management with systemctl/journalctl
# - binds only to 127.0.0.1:8000 so only Nginx can reach it
GUNICORN_WORKERS=$(( $(nproc) * 2 + 1 ))
[ "$GUNICORN_WORKERS" -gt 8 ] && GUNICORN_WORKERS=8

echo "==> Writing Gunicorn systemd unit..."
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

# Reload systemd after creating/modifying unit files so systemctl sees the new definition.
systemctl daemon-reload

# ── Nginx global rate-limit zones ────────────────────────────────────────
# These definitions must live in the global http context, not inside the site config.
# Why:
# - limit_req_zone is only valid at the http level
# - the site config can then reference zone=rig and zone=ip safely
echo "==> Writing Nginx global rate-limit definitions..."
cat > /etc/nginx/conf.d/gpu_monitor_rate_limits.conf << 'EOF'
limit_req_zone $http_x_rig_uuid zone=rig:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=ip:10m rate=30r/s;
EOF

# ── Nginx site configuration ─────────────────────────────────────────────
# Copy the template, replace placeholder domain, enable the site, disable the default site,
# then test the config before reloading.
# Why:
# - sites-available holds stored configs
# - sites-enabled contains symlinks for active configs
# - nginx -t prevents a broken config from being loaded
echo "==> Configuring Nginx..."
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/gpu_monitor
sed -i "s/monitor.example.com/$DOMAIN/g" /etc/nginx/sites-available/gpu_monitor
ln -sf /etc/nginx/sites-available/gpu_monitor /etc/nginx/sites-enabled/gpu_monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
systemctl enable nginx

# ── TLS certificate with Certbot ─────────────────────────────────────────
# Ask Let's Encrypt for a certificate using the Nginx plugin.
# Why this way:
# - Certbot can validate domain ownership through Nginx
# - --redirect adds HTTP -> HTTPS redirect automatically
# - keeping this in the script gives a near one-command deployment
#
# Note:
# - the domain must already resolve publicly to this server path
# - ports 80 and 443 must be reachable
# - if DNS is behind Cloudflare, HTTP validation must still be able to reach the origin
echo "==> Obtaining TLS certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@$DOMAIN" --redirect || {
    echo "⚠️  Certbot failed. Run manually later:"
    echo "   certbot --nginx -d $DOMAIN"
}

# ── Firewall ─────────────────────────────────────────────────────────────
# Basic host firewall:
# - deny incoming by default
# - allow outgoing by default
# - allow SSH so you do not lock yourself out
# - allow HTTP/HTTPS for the website and Certbot challenge/traffic
echo "==> Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

# ── Start/enable services ────────────────────────────────────────────────
# Enable and start Gunicorn now that config files are in place.
# PostgreSQL and Nginx are also enabled to start after reboot.
echo "==> Enabling and starting services..."
systemctl enable gunicorn
systemctl restart gunicorn
systemctl enable postgresql nginx

echo ""
echo "═══════════════════════════════════════════════════════"
echo "  ✅  DEPLOYMENT COMPLETE"
echo "═══════════════════════════════════════════════════════"
echo ""
echo "  Dashboard:    https://$DOMAIN/dashboard/rigs/"
echo "  Health:       https://$DOMAIN/api/v1/health/"
echo "  Admin panel:  https://$DOMAIN/admin/"
echo "  Allowed hosts: $DJANGO_ALLOWED_HOSTS_VALUE"
echo ""
echo "  Create an admin user:"
echo "    sudo -u $APP_USER bash -c 'cd $APP_DIR && source venv/bin/activate && set -a && source .env && set +a && python manage.py createsuperuser'"
echo ""
echo "  Useful commands:"
echo "    systemctl status gunicorn"
echo "    systemctl status nginx"
echo "    systemctl status postgresql"
echo "    journalctl -u gunicorn -n 100 --no-pager"
echo "    tail -f $APP_DIR/logs/gunicorn-error.log"
echo "═══════════════════════════════════════════════════════"
