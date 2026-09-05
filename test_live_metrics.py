"""
Test the _fetch_rig_metrics function to find why Live Metrics is empty.
"""
import os
import sys
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
sys.path.insert(0, '/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor')
django.setup()

from django.contrib.auth.models import User
from django.test import Client
from rigs.models import Rig
from metrics_app.models import LatestSnapshot, LatestDockerContainer
import uuid as uuid_module

# Find the test rig
target_uuid = "67462048-dbd9-42bc-9987-d3b48d0a817d"
try:
    rig = Rig.objects.get(uuid=target_uuid)
    print(f"Found rig: {rig.name}, owner={rig.owner.username}, status={rig.status}")
except Rig.DoesNotExist:
    print(f"Rig with UUID {target_uuid} does not exist")
    # Try to find any rig
    rigs = Rig.objects.all()[:3]
    for r in rigs:
        print(f"  {r.uuid} {r.name} owner={r.owner.username}")
    sys.exit(1)

# Check for LatestSnapshot
try:
    snap = LatestSnapshot.objects.get(rig_uuid=target_uuid)
    print(f"LatestSnapshot found:")
    print(f"  cpu_model={snap.cpu_model}")
    print(f"  cpu_utilization_pct={snap.cpu_utilization_pct}")
    print(f"  cpu_temp_c={snap.cpu_temp_c}")
    print(f"  gpu_count={snap.gpu_count}")
    print(f"  storage_count={snap.storage_count}")
    print(f"  network_count={snap.network_count}")
    print(f"  gpu_processes_json={snap.gpu_processes_json[:2] if snap.gpu_processes_json else 'EMPTY'}")
    print(f"  has_active_job={snap.has_active_job}")
    print(f"  timestamp={snap.timestamp}")
    print(f"  last_seen={rig.last_seen}")
except LatestSnapshot.DoesNotExist:
    print("No LatestSnapshot for this rig (no heartbeat yet)")

# Try the actual view with a test client
print("\n=== Testing htmx_metrics view ===")
try:
    from accounts.models import User
    from django.test import Client
    from django.test.client import Client
    import django
    # Allow testserver host
    from django.conf import settings
    settings.ALLOWED_HOSTS = ['*']

    owner = User.objects.get(id=rig.owner_id)
    client = Client(SERVER_NAME='localhost')
    client.force_login(owner)
    response = client.get(f'/dashboard/rigs/{target_uuid}/htmx-metrics/',
                          HTTP_HX_REQUEST='true', SERVER_NAME='localhost')
    import traceback
    print(f"Response status: {response.status_code}")
    if response.status_code == 200:
        print(f"Response length: {len(response.content)}")
        print(f"First 500 chars: {response.content[:500]}")
    else:
        print(f"Response content: {response.content[:500]}")
except Exception as e:
    traceback.print_exc()
    print(f"Exception: {e}")
    traceback.print_exc()
