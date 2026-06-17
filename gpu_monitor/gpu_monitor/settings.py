"""
Django settings for gpu_monitor project.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-dgh%o#sc)en+d9xdisy2+v3mzs(+jyzsmyh5s_dp-f%dtb!1wo'
)

DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_htmx',
    'rest_framework',
    'accounts',
    'rigs',
    'metrics_app',
    'dashboard',
    'audit',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
    'audit.middleware.AuditMiddleware',
]

ROOT_URLCONF = 'gpu_monitor.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'gpu_monitor.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'gpu_monitor'),
        'USER': os.environ.get('DB_USER', 'gpu_monitor'),
        'PASSWORD': os.environ.get('DB_PASSWORD', 'gpu_monitor'),
        'HOST': os.environ.get('DB_HOST', '127.0.0.1'),
        'PORT': os.environ.get('DB_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/rigs/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [],
    'DEFAULT_PERMISSION_CLASSES': [],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '10/min',
        'ingest': '2/min',
    },
}

# Session / cookie settings for IP address access
# Allow session cookies to work with raw IP addresses (no domain restriction)
SESSION_COOKIE_SECURE = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = False
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_TRUSTED_ORIGINS = ['http://*', 'https://*']
SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# ── Email Configuration ──────────────────────────────────────────────────────
# Default: console backend (prints emails to terminal) — safe for development.
# For production with Gmail SMTP, set these environment variables:
#   EMAIL_HOST=smtp.gmail.com
#   EMAIL_PORT=587
#   EMAIL_USE_TLS=true
#   EMAIL_HOST_USER=youragent@gmail.com
#   EMAIL_HOST_PASSWORD=xxxx xxxx xxxx xxxx   (16-char app-specific password)
#   DEFAULT_FROM_EMAIL=noreply@yourdomain.com
#
# Gmail setup:
#   1. Enable 2-Factor Authentication on the Google account
#   2. Generate App Password: https://myaccount.google.com/apppasswords
#      Select app: "Mail", Select device: "Other (Custom name)" → "GPU Rig Monitor"
#      Copy the 16-character password (spaces are for display only)
#   3. Use that password as EMAIL_HOST_PASSWORD
#   4. Sending limit: ~500 emails/day for free Gmail accounts
#
# To switch from console to SMTP, set EMAIL_HOST — if EMAIL_HOST is empty,
# the console backend is used automatically.
_email_host = os.environ.get('EMAIL_HOST', '')
if _email_host:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = _email_host
    EMAIL_PORT = int(os.environ.get('EMAIL_PORT', '587'))
    EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'true').lower() in ('true', '1', 'yes')
    EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
else:
    EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
    EMAIL_HOST = 'localhost'
    EMAIL_PORT = 587
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = ''
    EMAIL_HOST_PASSWORD = ''

DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@gpurgmonitor.local')
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Only configure file logging if the log directory is writable
_log_dir = BASE_DIR / 'logs'
_log_file = _log_dir / 'app.log'
_handlers = ['console']
try:
    _log_dir.mkdir(parents=True, exist_ok=True)
    # Check if we can write to the log file (or create it)
    if _log_file.exists():
        # File exists — check if writable by trying to open for append
        with open(str(_log_file), 'a'):
            pass
    else:
        _log_file.touch()
    _handlers = ['file', 'console']
except (OSError, PermissionError):
    pass  # Log directory/file not writable, use console only

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            'format': '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(_log_file),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'json',
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'json',
        },
    },
    'root': {
        'handlers': _handlers,
        'level': 'INFO',
    },
}
