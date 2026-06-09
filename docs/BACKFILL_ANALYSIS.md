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
| metrics_error_event_occurrence | 23,144 | Error events (9,892 distinct errors across 5 rigs) |
| **TOTAL** | **45,849** | |

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
| Error occurrences | 23,144 | 61,717 | 1,974,944 |
| **TOTAL** | **45,849** | **122,264** | **3,912,416** |

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

### Step 2: Insert Parent Snapshots
- For each repetition, shift all timestamps back by `rep × 9 hours`
- Use individual INSERT with RETURNING to capture new IDs
- Build `old_id → new_id` mapping for child table FK resolution
- Add temp `_backfill_old_id` column to track mapping (cleaned up after)

### Step 3: Insert Child Tables
- For each child row, look up the new snapshot_id from the mapping
- Shift timestamps by the same offset
- Batch insert in groups of 2000 for performance

### Step 4: Insert Error Occurrences
- Error occurrences have no FK dependency on snapshots
- Simply shift timestamps and insert
- The `error_event_id` references `metrics_lasterrors` which already exists

### Step 5: Handle Remaining Hours
- For the final 3 hours, only use source data from the last 3h of the window
- Same insertion logic as full repetitions

## Performance Considerations

### Batch Size
- Snapshots: individual INSERT with RETURNING (needed for ID mapping)
- Child tables: batched in groups of 2000
- Errors: batched in groups of 2000

### Estimated Time
- ~240K snapshot inserts × ~5ms each = ~20 minutes
- ~1.7M child row inserts × ~0.5ms each = ~15 minutes
- ~2M error inserts × ~0.3ms each = ~10 minutes
- **Total: ~45 minutes** for 3.9M rows

### Optimizations Applied
- Single read pass for source data
- Batch INSERT for child tables (no RETURNING needed)
- Temp column for ID mapping instead of per-row lookup
- Minimal ALTER TABLE (add/drop temp column once)

## Edge Cases Handled

### ON CONFLICT DO NOTHING
All INSERT statements use `ON CONFLICT DO NOTHING`:
- metrics_metricsnapshot: (rig_uuid, schema_version, timestamp)
- metrics_gpumetric: (rig_uuid, timestamp, gpu_index)
- metrics_storagemetric: (rig_uuid, timestamp, device)
- metrics_networkmetric: (rig_uuid, timestamp, interface)
- metrics_error_event_occurrence: (rig_uuid, timestamp, error_event_id)

This means:
- Rows that already exist are silently skipped (no error)
- Only genuinely new rows are inserted
- The script is idempotent — safe to re-run after partial failure
- No convoluted deduplication logic needed

### FK Relationships
- Parent snapshots inserted first (ON CONFLICT DO NOTHING)
- If a snapshot already exists, its old_id is NOT in the ID mapping
- Child rows for skipped snapshots are also skipped (they reference an old_id not in the mapping)
- Error occurrences reference existing metrics_lasterrors rows (no new errors created)

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
