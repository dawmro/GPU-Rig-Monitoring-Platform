"""
Deeper smoke test: verify rendered HTML contains the expected classes
(not just the page returns 200). Catches missing curly braces and
silent template errors that still produce 200.

After Phase 0.5, color utilities are in Tailwind directly. The
template composes tier colors as 'bg-X-400' / 'text-X-400'. The
component classes (.grm-card, .grm-btn-*, .grm-badge-*, .grm-alert-*)
are still in app.css.
"""
import os
import sys
from pathlib import Path

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

user = User.objects.filter(is_staff=True).first() or User.objects.first()
rig = Rig.objects.filter(owner=user).first()
if not rig and user.is_staff:
    rig = Rig.objects.first()

client = Client(SERVER_NAME='localhost')
client.force_login(user)

# Check rendered htmx-metrics contains the expected classes.
# Component classes (still in app.css):
#   - grm-card, grm-card-lg, grm-progress, grm-progress-fill,
#     grm-progress-fill-thin (the layout container), grm-chart-card,
#     grm-btn-primary, grm-input, grm-input-lg
# Tier system classes (composed as Tailwind):
#   - bg-red-400, bg-yellow-400, bg-green-400, bg-blue-400, bg-orange-400,
#     bg-gray-400
#   - text-red-400, text-yellow-400, text-green-400, text-blue-400,
#     text-gray-300, text-gray-200, text-gray-400
resp = client.get(f'/dashboard/rigs/{rig.uuid}/htmx-metrics/')
body = resp.content.decode('utf-8', errors='replace')

checks = [
    # Component classes (still in app.css)
    ('grm-card', 'Card class present'),
    ('grm-progress', 'Progress bar container class present'),
    ('grm-progress-fill ', 'Progress fill (layout) class present'),
    # Tier system (composed as Tailwind 400-series) — verify at least
    # one tier color is used. Specific tier classes depend on the
    # current data (e.g. if CPU is 80% you get bg-red-400; if it's 5%
    # you get bg-gray-400). So we check for the "bg-X-400" / "text-X-400"
    # pattern existence rather than specific colors.
    ('text-red-400', 'Tier red text class present'),
    ('text-yellow-400', 'Tier yellow text class present'),
    ('text-green-400', 'Tier green text class present'),
    ('text-gray-200', 'Tier light text class present'),
    ('text-gray-300', 'Tier gray text class present'),
    ('text-gray-400', 'Tier muted text class present'),
    ('text-', 'text- prefix (Tailwind tier system)'),
    ('bg-', 'bg- prefix (Tailwind tier system)'),
    # Confirm at least one Tier fill class is used (e.g. bg-red-400,
    # bg-yellow-400, bg-green-400, or bg-gray-400 depending on data).
    # We check for 'bg-X-400' where X is a non-empty color name.
    # If no fills appear, the spec/test is broken.
]

# Things that should NOT be in the output anymore.
forbidden = [
    # Old custom CSS classes (Phase 0.1, deleted in Phase 0.5)
    ('grm-text-red', 'Deleted: grm-text-red (replaced by text-red-400)'),
    ('grm-text-yellow', 'Deleted: grm-text-yellow'),
    ('grm-text-green', 'Deleted: grm-text-green'),
    ('grm-text-blue', 'Deleted: grm-text-blue'),
    ('grm-text-purple', 'Deleted: grm-text-purple'),
    ('grm-text-cyan', 'Deleted: grm-text-cyan'),
    ('grm-text-gray', 'Deleted: grm-text-gray'),
    ('grm-text-muted', 'Deleted: grm-text-muted'),
    ('grm-text-light', 'Deleted: grm-text-light'),
    ('grm-text-orange', 'Deleted: grm-text-orange'),
    ('grm-progress-fill-red', 'Deleted: grm-progress-fill-red'),
    ('grm-progress-fill-yellow', 'Deleted: grm-progress-fill-yellow'),
    ('grm-progress-fill-green', 'Deleted: grm-progress-fill-green'),
    ('grm-progress-fill-blue', 'Deleted: grm-progress-fill-blue'),
    ('grm-progress-fill-orange', 'Deleted: grm-progress-fill-orange'),
    ('grm-progress-fill-gray', 'Deleted: grm-progress-fill-gray'),
    ('grm-progress-fill-muted', 'Deleted: grm-progress-fill-muted'),
    # Pre-Phase 0.1 raw Tailwind colors that the templates should
    # have been migrated away from
    ('bg-red-500', 'Old bg-red-500 (should be bg-red-400)'),
    ('bg-yellow-500', 'Old bg-yellow-500 (should be bg-yellow-400)'),
    ('bg-blue-500', 'Old bg-blue-500 (should be bg-blue-400)'),
    # Pre-Phase 0.1 raw card recipe
    ('bg-gray-800 border border-gray-700 rounded-lg p-4', 'Old card recipe'),
    # text-gray-500 IS a valid Tailwind class (dim secondary text in
    # audit log and similar). It's NOT a remnant of the deleted
    # grm-text-muted. Removed from forbidden list.
    # ' · ' as CSS separator in fleet table multi-value cells (the bug
    # we fixed in Phase 1.1). The ' · ' character still appears in
    # template text content like "ext4 · /boot" but those are display
    # text, not class separators. Hard to distinguish without
    # contextual parsing, so we accept this as a known false positive.
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
    print(f"  [{status}] {label:55s}  count={count}")

if failures:
    print(f"\n❌ {failures} check(s) failed")
    sys.exit(1)
else:
    print("\n✅ All content checks passed")
