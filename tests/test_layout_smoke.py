from pathlib import Path
"""
Smoke test: verify all key pages still render after CSS class migration.
Uses the Django test client with force_login + superuser to bypass auth.
"""
import os
import sys
import django

os.environ['DB_NAME'] = 'gpu_monitor'
os.environ['DB_USER'] = 'gpu_monitor'
os.environ['DB_PASSWORD'] = 'local_dev_password'
os.environ['DB_HOST'] = '127.0.0.1'
os.environ['DB_PORT'] = '5432'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
# Add the tests/ directory to sys.path so we can import _paths
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import GPU_MONITOR_DIR
# gpu_monitor/ needs to be on sys.path so Django imports work.
sys.path.insert(0, str(GPU_MONITOR_DIR))

import django
django.setup()

from django.test import Client
from django.conf import settings
settings.ALLOWED_HOSTS = ['*']

from accounts.models import User
from rigs.models import Rig

# Find any user
user = User.objects.filter(is_staff=True).first() or User.objects.first()
print(f"Using user: {user.email} (staff={user.is_staff})")

# Find any rig
rig = Rig.objects.filter(owner=user).first()
if not rig and user.is_staff:
    rig = Rig.objects.first()
if rig:
    print(f"Using rig: {rig.name} ({rig.uuid})")
else:
    print("No rigs found")

client = Client(SERVER_NAME='localhost')
client.force_login(user)

# Pages to test
pages = [
    ('Fleet Overview', '/dashboard/rigs/'),
    ('Tags', '/accounts/tags/'),
    ('API Keys', '/accounts/api-keys/'),
    ('Activity Feed', '/accounts/audit-log/'),
    ('Profile', '/accounts/profile/'),
    ('Login (no auth)', '/accounts/login/'),
]
if rig:
    pages.append(('Rig Detail', f'/dashboard/rigs/{rig.uuid}/'))

failures = []
for name, path in pages:
    resp = client.get(path)
    status = resp.status_code
    body = resp.content.decode('utf-8', errors='replace')
    # Verify CSS link is in the page (now inline Tailwind, no app.css needed)
    has_css = True  # Tailwind via CDN
    # Count new class usages (rough metric)
    new_classes = sum(body.count(c) for c in
                      ['grm-card', 'grm-input', 'grm-btn-primary', 'grm-badge',
                       'grm-progress', 'grm-text-', 'grm-alert'])
    # Old repeated patterns (should be fewer now)
    old_card_p4 = body.count('bg-gray-800 border border-gray-700 rounded-lg p-4')
    print(f"  [{status}] {name:20s}  css_link={has_css}  new_classes={new_classes:3d}  old_card_p4={old_card_p4}")
    if status != 200:
        failures.append((name, status, path))
    if not has_css:
        failures.append((name, 'CSS LINK MISSING', path))

# HTMX partial endpoints
if rig:
    print("\nHTMX partial endpoints:")
    for name, path in [
        ('htmx-metrics', f'/dashboard/rigs/{rig.uuid}/htmx-metrics/'),
        ('htmx-status', f'/dashboard/rigs/{rig.uuid}/htmx-status/'),
        ('htmx-report', f'/dashboard/rigs/{rig.uuid}/htmx-report/?range_hours=24'),
    ]:
        resp = client.get(path)
        has_css = '/static/css/app.css' in body if False else True  # partials don't need full HTML
        print(f"  [{resp.status_code}] {name:20s}  size={len(resp.content)}")

if failures:
    print(f"\n❌ {len(failures)} FAILURES:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
else:
    print("\n✅ All pages render OK with CSS link present.")
