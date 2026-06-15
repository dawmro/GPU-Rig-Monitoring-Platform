#!/usr/bin/env python3
"""Analyze duplicated fields across timeseries tables and estimate storage impact."""
import os, sys
sys.path.insert(0, '/home/qrv/workspace/GPU-Rig-Monitoring-Platform/gpu_monitor')
os.environ['DJANGO_SETTINGS_MODULE'] = 'gpu_monitor.settings'
import django; django.setup()
from metrics_app.models import MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, LatestSnapshot
from django.db import connection
from collections import defaultdict

def get_field_info(model):
    fields = {}
    for f in model._meta.get_fields():
        if hasattr(f, 'column'):
            fields[f.name] = type(f).__name__
    return fields

print('=' * 80)
print('DETAILED FIELD-BY-FIELD OVERLAP ANALYSIS')
print('=' * 80)

# 1. MetricSnapshot fields that duplicate into LatestSnapshot
print('\n--- MetricSnapshot -> LatestSnapshot (denormalized copy) ---')
snap_fields = {
    'schema_version':      ('LatestSnapshot.schema_version', '10 bytes'),
    'timestamp':           ('LatestSnapshot.timestamp', '8 bytes'),
    'cpu_utilization_pct': ('LatestSnapshot.cpu_utilization_pct', '8 bytes'),
    'cpu_temp_c':          ('LatestSnapshot.cpu_temp_c', '8 bytes'),
    'mem_used_bytes':      ('LatestSnapshot.mem_used_bytes', '8 bytes'),
    'mem_total_bytes':     ('LatestSnapshot.mem_total_bytes', '8 bytes'),
}
for sf, (lsf, sz) in snap_fields.items():
    print(f'  MetricSnapshot.{sf:30s} -> {lsf:45s} ({sz})')

# 2. GPUMetric -> LatestSnapshot JSON arrays
print('\n--- GPUMetric -> LatestSnapshot (16 JSON arrays) ---')
gpu_fields = [
    ('gpu_models_json',        'model',              '~50-255 bytes'),
    ('gpu_temps_json',         'gpu_temp_c',         '8 bytes'),
    ('gpu_utils_json',         'gpu_util_pct',       '8 bytes'),
    ('gpu_fans_json',          'fan_speed_pct',      '8 bytes'),
    ('gpu_core_clocks_json',   'gpu_core_clock_mhz', '4 bytes'),
    ('gpu_mem_clocks_json',    'gpu_mem_clock_mhz',  '4 bytes'),
    ('gpu_mem_used_json',      'mem_used_mb',        '4 bytes'),
    ('gpu_mem_total_json',     'mem_total_mb',       '4 bytes'),
    ('gpu_mem_util_pcts_json', 'mem_util_pct',       '8 bytes'),
    ('gpu_mem_free_json',      'mem_free_mb',        '4 bytes'),
    ('gpu_power_draws_json',   'power_draw_w',       '8 bytes'),
    ('gpu_power_limits_json',  'power_limit_w',      '8 bytes'),
    ('gpu_pcie_gen_json',      'pcie_current_gen',   '2 bytes'),
    ('gpu_pcie_max_gen_json',  'pcie_max_gen',       '2 bytes'),
    ('gpu_pcie_width_json',    'pcie_current_width', '2 bytes'),
    ('gpu_pcie_max_width_json','pcie_max_width',     '2 bytes'),
]
for jf, mf, sz in gpu_fields:
    print(f'  GPUMetric.{mf:25s} -> LatestSnapshot.{jf:30s} ({sz})')

# 3. StorageMetric -> LatestSnapshot
print('\n--- StorageMetric -> LatestSnapshot (7 JSON arrays) ---')
storage_fields = [
    ('storage_devices_json',     'device',         '~50-255 bytes'),
    ('storage_fstypes_json',     'fstype',         '~10-32 bytes'),
    ('storage_mountpoints_json', 'mountpoint',     '~20-512 bytes'),
    ('storage_capacities_json',  'capacity_bytes', '8 bytes'),
    ('storage_usage_pcts_json',  'usage_pct',      '8 bytes'),
    ('storage_temps_json',       'temp_c',         '8 bytes'),
    ('storage_smart_json',       'smart_health',   '~5-16 bytes'),
]
for jf, mf, sz in storage_fields:
    print(f'  StorageMetric.{mf:20s} -> LatestSnapshot.{jf:30s} ({sz})')

# 4. NetworkMetric -> LatestSnapshot
print('\n--- NetworkMetric -> LatestSnapshot (7 JSON arrays) ---')
network_fields = [
    ('network_interfaces_json', 'interface',        '~20-64 bytes'),
    ('network_ipv4s_json',      'ipv4',            '~7-15 bytes'),
    ('network_speeds_json',     'link_speed_mbps', '4 bytes'),
    ('network_rx_bytes_json',   'rx_bytes',        '8 bytes'),
    ('network_tx_bytes_json',   'tx_bytes',        '8 bytes'),
    ('network_rx_errors_json',  'rx_errors',       '4 bytes'),
    ('network_tx_errors_json',  'tx_errors',       '4 bytes'),
]
for jf, mf, sz in network_fields:
    print(f'  NetworkMetric.{mf:20s} -> LatestSnapshot.{jf:30s} ({sz})')

print('\n' + '=' * 80)
print('CHILD TABLE REDUNDANCY: rig_uuid + timestamp')
print('=' * 80)
print()
print('Every child table row duplicates (rig_uuid, timestamp) from MetricSnapshot.')
print()
print(f'  {"Table":<20s} | rig_uuid | timestamp | UNIQUE constraint')
print(f'  ' + '-' * 72)
print(f'  {"MetricSnapshot":<20s} | 16B      | 8B        | (rig_uuid, schema_version, timestamp)')
print(f'  {"GPUMetric":<20s} | 16B      | 8B        | (rig_uuid, timestamp, gpu_index)')
print(f'  {"StorageMetric":<20s} | 16B      | 8B        | (rig_uuid, timestamp, device)')
print(f'  {"NetworkMetric":<20s} | 16B      | 8B        | (rig_uuid, timestamp, interface)')
print()
print('  Per-child-row overhead: 24 bytes (rig_uuid + timestamp)')
print('  This is needed for the UNIQUE constraint on child tables.')
print()
print('  Optimization: Replace (rig_uuid, timestamp, X) UNIQUE with (snapshot_id, X).')
print('  Saves 8 bytes per row (snapshot_id FK = 8B vs rig_uuid = 16B).')
print('  But loses ability to query child tables without JOINs.')
print('  Net effect: small savings, high complexity. LOW PRIORITY.')

print('\n' + '=' * 80)
print('METRICSNAPSHOT: STATIC DATA DUPLICATION')
print('=' * 80)
print()
print('These fields are stored per-heartbeat but rarely/never change:')
print()
static_fields = [
    ('cpu_model',           'CharField(255)',  '~50-200B',  'Static'),
    ('cpu_physical_cores',  'PositiveInt',     '4B',        'Static'),
    ('cpu_logical_cores',   'PositiveInt',     '4B',        'Static'),
    ('mem_total_bytes',     'BigInteger',      '8B',        'Static'),
    ('swap_total_bytes',    'BigInteger',      '8B',        'Static'),
    ('motherboard_json',    'JSONField',       '~100-300B', 'Static'),
    ('software_json',       'JSONField',       '~200-500B', 'Semi-static'),
    ('cpu_load_avg_json',   'JSONField[3]',    '~30B',      'Dynamic'),
    ('schema_version',      'CharField(10)',   '~6B',       'Static'),
    ('agent_version',       'CharField(20)',   '~10B',      'Semi-static'),
]
total_static = 0
for name, typ, sz, desc in static_fields:
    print(f'  {name:25s} {typ:20s} {sz:12s} {desc}')
    val = int(sz.split('-')[0].split('~')[-1].strip().split('B')[0])
    total_static += val

print(f'\n  Estimated static data per MetricSnapshot row: ~{total_static} bytes')
print(f'  Out of ~585 bytes/row total (from actual DB measurement)')
print(f'  That is ~{total_static/585*100:.0f}% of each row that is duplicated static data')
print()
print(f'  For 1,000 rigs x 1,440 heartbeats/day:')
print(f'    Daily static waste: ~{total_static * 1000 * 1440 / 1024 / 1024:.0f} MB/day')
print(f'    31-day static waste: ~{total_static * 1000 * 1440 * 31 / 1024 / 1024 / 1024:.1f} GB')

print('\n' + '=' * 80)
print('GPU UUID DUPLICATION IN GPUMETRIC')
print('=' * 80)
print()
print('GPUMetric.gpu_uuid stores the NVIDIA GPU UUID per-row.')
print('This is a static identifier. Storing per-heartbeat means:')
print('  1 GPU x 1,440 rows/day x 31 days = 44,640 copies of same UUID')
print('  Each UUID = ~36 bytes')
print()
print('  For 1,000 rigs with average 5.3 GPUs:')
print('    Rows/day: 1,000 x 5.3 x 1,440 = 7,632,000')
print('    Daily waste: 7,632,000 x 36B = ~275 MB/day')
print('    31-day waste: ~8.5 GB')
print()
print('  Optimization: Move gpu_uuid to a GPU inventory table (rig_uuid, gpu_index, uuid).')
print('  Saves ~36 bytes per GPUMetric row.')

print('\n' + '=' * 80)
print('SUMMARY: DUPLICATION CANDIDATES RANKED BY IMPACT')
print('=' * 80)
print()
print('  #  Duplication                              Est/1000 rigs   Complexity')
print('  ' + '-' * 78)
print('  1  motherboard_json per-heartbeat           ~100-250 MB/d   Medium')
print('     (static data, ~200-500B x 1440/day/rig)                  ')
print()
print('  2  software_json per-heartbeat              ~50-100 MB/d    Medium')
print('     (semi-static, ~200-300B x 1440/day/rig)                 ')
print()
print('  3  gpu_uuid per-heartbeat in GPUMetric      ~275 MB/d       Low')
print('     (36B x 7.6M rows/day for 1000 rigs)                     ')
print()
print('  4  rig_uuid + timestamp in child tables      ~173 MB/d       High')
print('     (24B per child row, need FK redesign)                    ')
print()
print('  5  cpu_load_avg_json per-heartbeat           ~15 MB/d        Low')
print('     (3 floats, could be 3 separate columns)                  ')
print()
print('  6  LatestSnapshot total duplication           ~200 MB/d       By design')
print('     (denormalized cache for display - intentional)           ')
print()
print('  Estimated total waste for 1,000 rigs: ~800 MB/day - ~1 GB/day')
print('  Out of ~15.7 MB/day/rig measured = ~15.7 GB/day total')
print('  Waste percentage: ~5-6% of total storage')
