from pathlib import Path
"""
Deeper smoke test: verify rendered HTML actually contains the new CSS
classes (not just the page returns 200). Catches missing curly braces
and silent template errors that still produce 200.
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
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'gpu_monitor'))

import django
django.setup()

from django.test import Client
from django.conf import settings
settings.ALLOWED_HOSTS = ['*']

from accounts.models import User
from rigs.models import Rig

user = User.objects.filter(is_staff=True).first() or User.objects.first()
rig = Rig.objects.filter(owner=user).first()
if not rig and user.is_staff:
    rig = Rig.objects.first()

client = Client(SERVER_NAME='localhost')
client.force_login(user)

# Check rendered htmx-metrics contains grm-card and grm-text-* classes
resp = client.get(f'/dashboard/rigs/{rig.uuid}/htmx-metrics/')
body = resp.content.decode('utf-8', errors='replace')

checks = [
    ('grm-card', 'Card class present'),
    ('grm-text-light', 'Light text class present'),
    ('grm-text-gray', 'Gray text class present'),
    ('grm-text-red', 'Red threshold class present'),
    ('grm-text-yellow', 'Yellow threshold class present'),
    ('grm-text-green', 'Green threshold class present'),
    ('grm-progress', 'Progress bar class present'),
    ('grm-progress-fill', 'Progress fill class present'),
    ('grm-text-cyan', 'Cyan text class present'),
    ('grm-text-', 'grm-text- prefix present (any variant)'),
]

# Things that should NOT be in the output
forbidden = [
    ('bg-gray-800 border border-gray-700 rounded-lg p-4 mb-4', 'Old card recipe (should be replaced)'),
    ('text-gray-300', 'Old text-gray-300 (should be grm-text-light)'),
    ('text-gray-400', 'Old text-gray-400 (should be grm-text-gray)'),
    ('text-red-400', 'Old text-red-400 (should be grm-text-red)'),
    ('text-yellow-400', 'Old text-yellow-400 (should be grm-text-yellow)'),
    ('text-green-400', 'Old text-green-400 (should be grm-text-green)'),
    ('bg-red-500', 'Old bg-red-500 (should be grm-progress-fill-red)'),
    ('bg-yellow-500', 'Old bg-yellow-500 (should be grm-progress-fill-yellow)'),
    ('bg-blue-500', 'Old bg-blue-500 (should be grm-progress-fill-blue)'),
]

print("htmx-metrics rendered output:")
print(f"  Size: {len(body)} bytes")
failures = 0

for needle, label in checks:
    count = body.count(needle)
    status = "✓" if count > 0 else "✗"
    if count == 0:
        failures += 1
    print(f"  [{status}] {label:50s}  count={count}")

for needle, label in forbidden:
    count = body.count(needle)
    status = "✓" if count == 0 else "✗"
    if count > 0:
        failures += 1
    print(f"  [{status}] {label:50s}  count={count}")

if failures:
    print(f"\n❌ {failures} check(s) failed")
    sys.exit(1)
else:
    print("\n✅ All content checks passed")
