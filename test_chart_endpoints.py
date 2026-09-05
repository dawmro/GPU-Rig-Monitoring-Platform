"""
Test the chart endpoints to find why charts show no data.
"""
import os
import sys
import django
import traceback

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'gpu_monitor.settings')
sys.path.insert(0, '/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor')
os.environ['DB_NAME'] = 'gpu_monitor'
os.environ['DB_USER'] = 'gpu_monitor'
os.environ['DB_PASSWORD'] = 'local_dev_password'
os.environ['DB_HOST'] = '127.0.0.1'
os.environ['DB_PORT'] = '5432'
django.setup()

from django.conf import settings
settings.ALLOWED_HOSTS = ['*']

from rigs.models import Rig
from accounts.models import User
from django.test import Client

# Find a test rig with multi-disk data
online_rigs = Rig.objects.filter(status='online')
test_rig = None
for r in online_rigs:
    test_rig = r
    print(f"Testing rig: {r.uuid} {r.name} status={r.status}")
    break

if not test_rig:
    print("No online rig found")
    # Fall back to any rig
    test_rig = Rig.objects.first()
    print(f"Falling back to: {test_rig.uuid} {test_rig.name}")

target_uuid = str(test_rig.uuid)
owner = User.objects.get(id=test_rig.owner_id)
client = Client(SERVER_NAME='localhost')
client.force_login(owner)

# Test various chart endpoints
test_cases = [
    ('cpu_utilization_pct', 24, ''),
    ('cpu_temp_c', 24, ''),
    ('cpu_load_avg', 24, ''),
    ('gpu_temp_c', 24, ''),
    ('gpu_util_pct', 24, ''),
    ('disk_usage_pct', 24, ''),
    ('disk_read_bytes_delta', 24, ''),
    ('disk_usage_pct', 24, 'multi_disk=true'),
    ('disk_read_bytes_delta', 24, 'multi_disk=true'),
    ('disk_utilization_pct', 24, ''),
    ('disk_utilization_pct', 24, 'multi_disk=true'),
    ('net_rx_bytes_delta', 24, ''),
    ('net_rx_bytes_delta', 24, 'multi_iface=true'),
    ('mem_used_bytes', 24, ''),
    ('mem_used_bytes', 24, 'multi_mem=true'),
]

for metric, range_h, extra in test_cases:
    url = f'/api/v1/rigs/{target_uuid}/chart-data/?metric={metric}&range={range_h}&bucket_minutes=1&{extra}'
    print(f'\n  URL: {url}')
    try:
        response = client.get(url, SERVER_NAME='localhost')
        data = response.json() if response.status_code == 200 else None
        if data:
            datasets = data.get('datasets', [])
            n_datasets = len(datasets)
            n_data_points = sum(len(d.get('data', [])) for d in datasets)
            n_non_null = sum(
                sum(1 for v in d.get('data', []) if v is not None)
                for d in datasets
            )
            dataset_labels = [d.get('label', '?') for d in datasets]
            print(f"  {metric} ({extra or 'default'}): {n_datasets} ds [{', '.join(dataset_labels)}], {n_data_points} pts, {n_non_null} non-null")
            if n_datasets == 0 or n_non_null == 0:
                print(f"    !!! NO DATA")
        else:
            print(f"  {metric} ({extra or 'default'}): status={response.status_code}")
    except Exception as e:
        print(f"  {metric} ({extra or 'default'}): EXCEPTION {e}")
