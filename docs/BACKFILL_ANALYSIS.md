# Backfill Historical Data — Deep Analysis

## Problem Statement
Populate the database with 32 days of historical data for testing charts,
data retention, and compaction. The source is the last 9 hours of real data.

## Data Analysis

### Current State (9-hour window)
| Table | Rows | Description |
|---|---|---|
| metrics_metricsnapshot | 2,807 | Parent rows (CPU, memory, status) |
| metrics_gpumetric | 7,801 | Per-GPU metrics (5 rigs, 1-8 GPUs each) |
| metrics_storagemetric | 7,679 | Per-disk metrics (1-5 disks per rig) |
| metrics_networkmetric | 4,418 | Per-interface metrics (1-4 ifaces per rig) |
| error_count per snapshot | ~3,683 total | Error frequency data (carried forward per snapshot) |
| **TOTAL** | **22,705** | (plus error data on each snapshot) |

### Rig Inventory (5 active rigs)
| Rig | GPUs | Disks | Ifaces | Snapshots |
|---|---|---|---|---|
| ubuntu-vm | 0 | 1 | 1 | 907 |
| win-10-rtx-3060 | 1 | 5 | 4 | 537 |
| ZET2559 | 8 | 3 | 1 | 454 |
| rig5090Gigabyte | 1 | 3 | 1 | 454 |
| ZET2558 | 7 | 3 | 1 | 454 |

### Volume Projection (32 days)
| Metric | Per 9h | Per Day | 32 Days |
|---|---|---|---|
| Snapshots | 2,807 | 7,485 | 239,520 |
| GPU rows | 7,801 | 20,803 | 665,696 |
| Disk rows | 7,679 | 20,477 | 655,264 |
| Network rows | 4,418 | 11,781 | 376,992 |
| **TOTAL** | **22,705** | **59,863** | **1,915,616** |

## Repetition Strategy

### Approach: Time-Shifted Replication
The 9-hour source window is repeated 85 times (765 hours = 31.875 days),
with each repetition shifted back by 9 hours. A final partial repetition
covers the remaining 3 hours.

```
Rep  1: [now-18h, now-9h]   ← source shifted back 9h
Rep  2: [now-27h, now-18h]  ← source shifted back 18h
...
Rep 85: [now-765h, now-756h] ← source shifted back 765h
Remainder: [now-768h, now-765h] ← last 3h of source shifted back 768h
```

### Why This Works
- **Realistic patterns**: GPU temps, utilization, and power draw patterns are preserved
- **Chronological ordering**: Timestamps are monotonically increasing (oldest first)
- **FK integrity**: Parent→child relationships are maintained via ID mapping
- **Chart compatibility**: All chart queries (24h, 7d, 30d) work correctly

## Implementation Details

### Step 1: Read Source Data
- Single SELECT per table with `timestamp >= now - 9h`
- Ordered by timestamp for chronological processing
- All columns read to preserve data fidelity
- Tables: MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric

### Step 2: Insert Parent Snapshots
- For each repetition, shift all timestamps back by `rep × 9 hours`
- Use individual INSERT with RETURNING to capture new IDs
- Build `old_id → new_id` mapping for child table FK resolution
- Add temp `_backfill_old_id` column to track mapping (cleaned up after)

### Step 3: Insert Child Tables (with FK to MetricSnapshot)
- For each child row, look up the new snapshot_id from the mapping
- Shift timestamps by the same offset
- Batch insert in groups of 5000 for performance
- Tables: GPUMetric, StorageMetric, NetworkMetric

### Step 3b: Insert Docker Container Metrics (no FK to MetricSnapshot)
- DockerContainerMetric is an independent time-series table (no snapshot_id FK)
- Indexed by (rig_uuid, timestamp, name)
- Shift timestamps by the same offset
- Inserted via `ON CONFLICT (rig_uuid, timestamp, name) DO NOTHING`

### Step 4: Handle Remaining Hours
- For the final hours, only use source data from the last N hours of the window
- Same insertion logic as full repetitions (including docker container metrics)

## Performance

### Current Implementation (execute_values + ALTER TABLE for ID mapping)
- **Rate**: ~20,000-25,000 rows/second
- **32 days** (12h source): ~4.2M rows in ~3 minutes
- Uses `ALTER TABLE ADD COLUMN _bf_old_id` per repetition for ID mapping
- Child rows inserted with `ON CONFLICT DO NOTHING`

### Progress Output Example
```
Rep   1/64  (  1.6%)  shift   12h  + 66,172 rows  total       66,172  20,251 rows/s  elapsed 3s  ETA 3m 25s
Rep  32/64  ( 50.0%)  shift  384h  + 66,172 rows  total    2,117,504  24,208 rows/s  elapsed 1m 34s  ETA 1m 34s
Rep  64/64  (100.0%)  shift  768h  + 66,172 rows  total    4,235,008  23,722 rows/s  elapsed 3m 4s  ETA 0s

Done! 4,235,008 rows inserted in 3m 4s (23,013 rows/s avg)
```

## ⚠️ Critical Warning: Child Row Timestamp Shifting

When modifying the backfill script, ensure child row timestamps are correctly shifted:

**The Bug**: If you change the data format (dict→tuple) or move timestamp arithmetic
from Python to SQL, you MUST ensure `_insert_child_rows` computes `new_ts = row['timestamp'] - offset`
for every child row. Using the raw/unshifted timestamp causes:

1. All N repetitions write child data to the SAME time slots
2. `ON CONFLICT DO NOTHING` keeps only the first repetition's data
3. Result: ~98% of GPU/disk/net data is silently lost

**Verification**: After backfill, check GPU/snap ratio:
```bash
python -c "
from metrics_app.models import MetricSnapshot, GPUMetric
snaps = MetricSnapshot.objects.filter(timestamp__lt=timezone.now()-timedelta(hours=1)).count()
gpus = GPUMetric.objects.filter(timestamp__lt=timezone.now()-timedelta(hours=1)).count()
print(f'GPU/snap ratio: {gpus/max(snaps,1):.2f} (expected ~3.0 for multi-GPU rigs)')
"
```
A ratio < 0.1 indicates the bug is present.

## Edge Cases Handled

### ON CONFLICT DO NOTHING
All INSERT statements use `INSERT ... ON CONFLICT DO NOTHING`:
- metrics_metricsnapshot: (rig_uuid, schema_version, timestamp)
- metrics_gpumetric: (rig_uuid, timestamp, gpu_index)
- metrics_storagemetric: (rig_uuid, timestamp, device)
- metrics_networkmetric: (rig_uuid, timestamp, interface)
- metrics_dockercontainermetric: (rig_uuid, timestamp, name)

This means:
- Rows that already exist are silently skipped (no error)
- Only genuinely new rows are inserted
- The script is idempotent — safe to re-run after partial failure
- No convoluted deduplication logic needed

### FK Relationships
- Parent snapshots inserted first (ON CONFLICT DO NOTHING)
- If a snapshot already exists, its old_id is NOT in the ID mapping
- Child rows for skipped snapshots are also skipped (they reference an old_id not in the mapping)

### Remaining Hours
- If target_days × 24 is not evenly divisible by source_hours, a final partial repetition fills the gap
- Only source rows from the last N hours of the source window are used for the partial repetition

## Usage

```bash
cd /opt/gpu_monitor
source venv/bin/activate && set -a && source .env && set +a

# Preview
python manage.py backfill_historical_data --dry-run

# Full 32-day backfill (default)
python manage.py backfill_historical_data

# Custom parameters
python manage.py backfill_historical_data --hours 6 --days 14
```

## Post-Backfill Verification

```bash
# Check data spans 32 days
python -c "
import os; os.environ['DJANGO_SETTINGS_MODULE'] = 'gpu_monitor.settings'
import django; django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute('SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM metrics_metricsnapshot')
    row = c.fetchall()[0]
    print(f'Snapshots: {row[0]:,} rows')
    print(f'Range: {row[1]} to {row[2]}')
    print(f'Span: {row[2] - row[1]}')
"

# Test compaction on the backfilled data
python manage.py compact_data --dry-run --verbose

# Test cleanup (should delete nothing if retention > 32 days)
python manage.py cleanup_old_data --days=31 --dry-run --verbose
```

## Cleanup

To remove all backfilled data:
```bash
# Delete everything older than 9 hours
python manage.py cleanup_old_data --days=0 --verbose
# Then manually delete the rest, or reset the database
```
