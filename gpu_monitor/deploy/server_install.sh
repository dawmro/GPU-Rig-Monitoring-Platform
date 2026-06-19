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
#   This is important for deployment scripts because silent failures create half-configured servers
#   that are much harder to debug than a script that simply stops at the first bad step.
# - We keep Gunicorn bound to 127.0.0.1:8000 so it is not exposed directly to the internet.
#   Nginx is the only public entry point, which gives us one place to handle TLS, buffering,
#   logging, rate-limiting, and request filtering.
# - We let Nginx be the only public web server on ports 80/443.
#   That is the standard reverse-proxy pattern for Django deployments.
# - We define Nginx rate-limit zones globally in conf.d because limit_req_zone is only valid
#   in the http context, not inside a server/location block.
# - We preserve existing .env secrets on rerun when possible, because rotating Django SECRET_KEY
#   on every deploy would invalidate existing sessions and signed values.
# - We now write Django security-related env vars into .env so settings.py can read them directly.
#   This keeps production security behavior configurable without hardcoding those values in Python.

set -euo pipefail

DOMAIN="${1:-monitor.example.com}"
APP_DIR="/opt/gpu_monitor"
APP_USER="monitoring"
DB_NAME="gpu_monitor"
DB_USER="gpu_monitor"

# Detect one local IPv4 address from the server.
# Why:
# - Django ALLOWED_HOSTS should explicitly allow the hostname users normally use
#   plus localhost and, if needed, the machine's own IP for direct access/testing.
# - "hostname -I" prints local interface addresses. We extract the first IPv4-looking token.
# - This is usually good enough for a simple VPS setup.
# - Caveat: on NATed/cloud setups, this may be a private/internal IP rather than a public IP.
#   That is still useful for local checks, but if you want a specific public IP host entry,
#   you can replace it manually later in .env.
SERVER_IP="$(hostname -I | awk '{for (i=1;i<=NF;i++) if ($i ~ /^[0-9]+\./) {print $i; exit}}')"

# Build the ALLOWED_HOSTS value as a comma-separated string.
# Why:
# - your settings.py parses DJANGO_ALLOWED_HOSTS using a comma split
# - ALLOWED_HOSTS must contain hostnames/IPs only, not full URLs or schemes
# - we include:
#   * the real domain
#   * 127.0.0.1 and localhost for local server-side checks
#   * the detected server IP when present for direct IP-based access
if [ -n "${SERVER_IP:-}" ]; then
    DJANGO_ALLOWED_HOSTS_VALUE="$DOMAIN,127.0.0.1,localhost,$SERVER_IP"
else
    DJANGO_ALLOWED_HOSTS_VALUE="$DOMAIN,127.0.0.1,localhost"
fi

# Build the CSRF_TRUSTED_ORIGINS value as full origins with scheme.
# Why:
# - Django expects CSRF_TRUSTED_ORIGINS entries to include protocol, e.g. https://example.com
# - this setting is different from ALLOWED_HOSTS: it validates origins for unsafe requests
#   like POST/PUT/PATCH/DELETE, especially important for admin login and forms behind HTTPS
# - we include both http and https variants:
#   * https is the desired steady-state production mode
#   * http can still be useful during initial setup/troubleshooting before TLS is live
# - after TLS is fully working, you may choose to remove the http entries for stricter policy
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
# Install all OS packages needed by the stack.
# Why each package is here:
# - python3 / python3-venv / python3-pip: Python runtime + isolated app environment support
# - postgresql / postgresql-contrib / postgresql-client: database server + extras + CLI tools
# - nginx: public reverse proxy in front of Gunicorn
# - certbot / python3-certbot-nginx: Let's Encrypt certificate automation via Nginx integration
# - ufw: host firewall management with simpler syntax than raw iptables/nftables
# - git / build-essential / curl: common admin/build/troubleshooting tools often needed later
echo "==> Installing system packages..."
apt update
apt install -y python3 python3-venv python3-pip postgresql postgresql-contrib \
    postgresql-client nginx certbot python3-certbot-nginx \
    ufw git build-essential curl

# Ensure PostgreSQL is both running right now and enabled across reboots.
# Why restart instead of only start:
# - restart is safe here and ensures we land in a known running state after package install
echo "==> Enabling and starting PostgreSQL..."
systemctl restart postgresql
systemctl enable postgresql

# ── Secrets loading / generation ──────────────────────────────────────────
# On reruns, try to reuse existing secrets from the current .env file.
# Why:
# - changing DJANGO_SECRET_KEY unnecessarily logs users out and invalidates signed data
# - preserving DB password reduces accidental drift between app config and database config
# - rerunnable deployment scripts are easier to maintain than one-time-only scripts
EXISTING_DB_PASS=""
EXISTING_DJANGO_SECRET=""

if [ -f "$APP_DIR/.env" ]; then
    echo "==> Existing .env found, attempting to reuse secrets..."
    EXISTING_DB_PASS="$(grep -E '^DB_PASSWORD=' "$APP_DIR/.env" | head -n1 | cut -d= -f2- || true)"
    EXISTING_DJANGO_SECRET="$(grep -E '^DJANGO_SECRET_KEY=' "$APP_DIR/.env" | head -n1 | cut -d= -f2- || true)"
fi

# Generate new secrets only when they do not already exist.
# Why these formats:
# - token_hex(24) gives a long random database password with shell-safe characters
# - token_urlsafe(50) gives a strong Django secret key suitable for signing
DB_PASS="${EXISTING_DB_PASS:-$(python3 -c "import secrets; print(secrets.token_hex(24))")}"
DJANGO_SECRET="${EXISTING_DJANGO_SECRET:-$(python3 -c "import secrets; print(secrets.token_urlsafe(50))")}"

# ── Database setup ────────────────────────────────────────────────────────
# Create or update PostgreSQL role/database in an idempotent way.
# Why this approach:
# - CREATE USER by itself would fail if the user already exists
# - the DO block lets us branch safely inside PostgreSQL
# - ALTER USER refreshes the password when needed
# - CREATE DATABASE is only executed when missing
# - GRANT ALL PRIVILEGES can be repeated safely
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
# Run the application as a dedicated low-privilege system account.
# Why:
# - never run Django/Gunicorn as root unless absolutely necessary
# - a separate service user limits damage if the app is compromised
# - ownership stays clear for logs, static files, venv, and source files
# - /usr/sbin/nologin prevents interactive login for this service account
if ! id "$APP_USER" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
    echo "Created user: $APP_USER"
fi

# Create the application directory early so later copy/chown operations have a stable target.
mkdir -p "$APP_DIR"

# ── Copy project files ────────────────────────────────────────────────────
# Copy the project into the final deployment path if the script is being run from the repo tree.
# Why:
# - systemd, Nginx, and the operational commands all assume one fixed app path
# - this avoids relying on whichever directory the admin happened to run the script from
# - we copy normal files and also hidden files like .gitignore/.env.example if present
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$SCRIPT_DIR" != "$APP_DIR" ] && [ -f "$SCRIPT_DIR/manage.py" ]; then
    echo "==> Copying project files to $APP_DIR..."
    cp -r "$SCRIPT_DIR"/* "$APP_DIR/"
    cp -r "$SCRIPT_DIR"/.[!.]* "$APP_DIR/" 2>/dev/null || true
fi

# Ensure the app directory belongs to the service user.
# Why:
# - Gunicorn and management commands run as APP_USER, so they must be able to read/write
#   expected directories such as logs and staticfiles
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# ── Log directories and static directories ────────────────────────────────
# Pre-create directories and files Django/Gunicorn will write to.
# Why:
# - avoids runtime PermissionError when the app first tries to write logs
# - makes file locations predictable and easier to document/support
# - explicit file creation also helps when a logging handler expects the path to exist
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

# STATIC_ROOT target for collectstatic.
# Why:
# - collectstatic needs a real destination directory
# - this keeps collected assets outside app packages and in one predictable place
mkdir -p "$APP_DIR/staticfiles"
chown "$APP_USER:$APP_USER" "$APP_DIR/staticfiles"

# ── Python virtualenv ─────────────────────────────────────────────────────
# Create/update a Python virtual environment owned by the application user.
# Why:
# - isolates project packages from Ubuntu's system Python packages
# - makes upgrades/reinstalls safer and more repeatable
# - reruns are cheap because we only create the venv if it does not exist
echo "==> Creating/updating Python virtualenv..."
sudo -u "$APP_USER" bash << 'APP'
cd /opt/gpu_monitor
if [ ! -d venv ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install --upgrade pip

# Install runtime dependencies directly.
# Why this simple approach:
# - keeps deployment self-contained even if requirements.txt is not yet maintained
# - useful for a controlled internal deployment script
# If you later maintain a requirements.txt, replace this with:
#   pip install -r requirements.txt
pip install django djangorestframework django-htmx psycopg2-binary argon2-cffi \
    gunicorn requests pyyaml psutil
APP

# ── Environment file ──────────────────────────────────────────────────────
# Write the runtime configuration consumed by Django and systemd.
# Why:
# - keeps secrets and deployment-specific values out of source code
# - EnvironmentFile= in systemd can load the same values Gunicorn/Django need
# - your settings.py can parse booleans and comma-separated lists from these env vars
# - security-related settings now live here, so changing deployment behavior does not
#   require editing Python code on the server
echo "==> Writing application .env..."
cat > "$APP_DIR/.env" << ENVEOF
DJANGO_SECRET_KEY=$DJANGO_SECRET
DJANGO_DEBUG=False

# Django host validation: hostnames/IPs only, comma-separated
DJANGO_ALLOWED_HOSTS=$DJANGO_ALLOWED_HOSTS_VALUE

# Django CSRF trusted origins: full origins with scheme, comma-separated
CSRF_TRUSTED_ORIGINS=$CSRF_TRUSTED_ORIGINS_VALUE

# HTTPS / cookie security
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE=Lax
CSRF_COOKIE_SAMESITE=Lax

# Reverse proxy support
# These are used by settings.py so Django correctly understands forwarded host/port
# and the original secure scheme when sitting behind Nginx.
USE_X_FORWARDED_HOST=True
USE_X_FORWARDED_PORT=True

# Extra browser-facing hardening
SECURE_CONTENT_TYPE_NOSNIFF=True
SECURE_BROWSER_XSS_FILTER=True
X_FRAME_OPTIONS=DENY
SECURE_REFERRER_POLICY=same-origin

# HTTP Strict Transport Security
# Why:
# - tells browsers to prefer HTTPS for future requests
# - 31536000 = 1 year
# Start conservatively if you are still testing HTTPS behavior.
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=False
SECURE_HSTS_PRELOAD=False

# Database connection
DB_NAME=$DB_NAME
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASS
DB_HOST=127.0.0.1
DB_PORT=5432
ENVEOF
chmod 600 "$APP_DIR/.env"
chown "$APP_USER:$APP_USER" "$APP_DIR/.env"

# ── Django migrations and static files ────────────────────────────────────
# Run schema migrations and collect static assets before starting/restarting the app.
# Why:
# - migrate ensures the database structure matches the current code
# - collectstatic gathers assets into STATIC_ROOT for predictable serving
# - using "set -a; source .env; set +a" exports env vars from the file into this shell
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
# Size the worker count from CPU count, but cap it to avoid going too high on bigger boxes.
# Why:
# - the classic rough rule is (2 * CPU) + 1
# - capping at 8 prevents overcommitting memory/CPU on small-to-medium deployments
GUNICORN_WORKERS=$(( $(nproc) * 2 + 1 ))
[ "$GUNICORN_WORKERS" -gt 8 ] && GUNICORN_WORKERS=8

# systemd unit for Gunicorn.
# Why:
# - systemd handles restart-on-failure, boot startup, logs, and process lifecycle
# - EnvironmentFile points Gunicorn/Django to the same .env we just wrote
# - bind stays on 127.0.0.1 because only Nginx should talk to Gunicorn directly
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

# Tell systemd to reread unit files after we changed/created gunicorn.service.
echo "==> Reloading systemd units..."
systemctl daemon-reload

# ── Nginx global rate-limit zones ────────────────────────────────────────
# Global Nginx rate-limit zones must be defined in http context.
# Why:
# - limit_req_zone is not valid inside a site/server block
# - once defined here, your site config can reference zone=rig and zone=ip
# - this keeps reusable rate-limit primitives separate from the site definition
echo "==> Writing Nginx global rate-limit definitions..."
cat > /etc/nginx/conf.d/gpu_monitor_rate_limits.conf << 'EOF'
limit_req_zone $http_x_rig_uuid zone=rig:10m rate=5r/m;
limit_req_zone $binary_remote_addr zone=ip:10m rate=30r/s;
EOF

# ── Nginx site configuration ─────────────────────────────────────────────
# Install the Nginx vhost template and replace the placeholder domain with the real one.
# Why:
# - storing the template in the repo keeps infra config versioned with the app
# - symlink from sites-enabled activates the site in the standard Debian/Ubuntu style
# - removing the default site avoids accidental conflicts or default-page exposure
# - nginx -t validates config before restart so we do not blindly reload a broken file
echo "==> Configuring Nginx..."
cp "$APP_DIR/deploy/nginx.conf" /etc/nginx/sites-available/gpu_monitor
sed -i "s/monitor.example.com/$DOMAIN/g" /etc/nginx/sites-available/gpu_monitor
ln -sf /etc/nginx/sites-available/gpu_monitor /etc/nginx/sites-enabled/gpu_monitor
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
systemctl enable nginx

# ── TLS certificate with Certbot ─────────────────────────────────────────
# Obtain a Let's Encrypt certificate using the Nginx plugin.
# Why this way:
# - Certbot can edit Nginx config and install redirects automatically
# - --redirect makes plain HTTP requests upgrade to HTTPS once the certificate is installed
# - using Certbot with Nginx is a common low-friction production setup
#
# Notes:
# - the domain must already resolve to this server
# - ports 80 and 443 must be open from the internet
# - the temporary HTTP challenge must be able to reach Nginx successfully
echo "==> Obtaining TLS certificate..."
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@$DOMAIN" --redirect || {
    echo "⚠️  Certbot failed. Run manually later:"
    echo "   certbot --nginx -d $DOMAIN"
}

# ── Firewall ─────────────────────────────────────────────────────────────
# Configure a simple host firewall.
# Why:
# - default deny on inbound traffic reduces accidental exposure
# - outgoing stays allowed so package installs, DNS, SMTP, etc. continue to work
# - SSH is kept open to avoid locking yourself out
# - HTTP/HTTPS are the only public web ports we expect for this app
echo "==> Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

# ── Start/enable services ────────────────────────────────────────────────
# Enable and start the services after all config is in place.
# Why this order:
# - systemd unit and env file must exist before gunicorn starts
# - nginx should already validate and restart successfully before final output
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
echo "  CSRF origins:  $CSRF_TRUSTED_ORIGINS_VALUE"
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
