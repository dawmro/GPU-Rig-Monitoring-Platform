# Data Retention — Implementation Reference

## Measured Database Usage

### Current State (7 days, 5 active rigs at ~50% uptime)
- Total metric tables: 81.8 MB
- Daily insertion per rig: ~4.7 MB (at 100% uptime)
- 5 active rigs produce ~23.5 MB/day at 100% uptime

### Per-Rig Storage (100% uptime)
| Period | Storage |
|---|---|
| 1 day | 4.7 MB |
| 7 days | 32.9 MB |
| 30 days | 141 MB |
| 31 days | 146 MB |

### Projected Storage for 1,000 Rigs
| Retention | Raw Storage | After Compaction |
|---|---|---|
| 1 day | 4.7 GB | 4.7 GB |
| 7 days | 32.9 GB | 6.9 GB |
| 31 days | 146 GB | ~9 GB |

---

## Retention Strategy: Tiered Compaction

### Tier 1: Raw Data (0-1 day)
- Keep all per-minute data unchanged
- Needed for Live Metrics and 24h charts (1-minute buckets)

### Tier 2: 1-Hour Buckets (1-31 days)
- Compact data older than 1 day into 1-hour buckets
- Reduces 1,440 rows/day to 24 rows/day (60× savings)
- 7d and 30d charts use 1-hour buckets

### Tier 3: Delete (31+ days)
- Remove all data older than 31 days
- 31 days provides 1-day safety margin beyond the 30-day max chart range

---

## Space Savings Calculation

### Without Compaction
31 days x 4.7 MB/day x 1,000 rigs = **145.7 GB**

### With Tiered Compaction

| Tier | Period | Raw Size | Factor | Compact Size |
|---|---|---|---|---|
| Raw | Day 0-1 | 4.7 GB | 1x | 4.7 GB |
| 1-hour | Day 1-31 | 141.3 GB | 60x | 2.4 GB |
| **Total** | **31 days** | **146.0 GB** | | **~7 GB** |

**95% storage reduction** through compaction.

---

## Management Commands

### `compact_data` — Aggregate Old Data into Larger Buckets

**Purpose:** Reduces storage by aggregating per-minute rows into 1-hour buckets.

**How It Works:**

Single phase:

**Phase 1** (data > 1 day old):
- Creates 1-hour buckets from per-minute data
- Groups rows by `rig_uuid` (and `gpu_index`, `device`, etc.) into 1-hour windows
- Applies aggregation per metric type:
  - `AVG` for gauges: temperature, utilization, power, memory usage, GPU core/memory clock
  - `SUM` for counters: network byte deltas, error counts
  - `LAST` for static fields: model names, UUIDs, capacity, PCIe link info
- Deletes original per-minute rows, inserts aggregated rows

**Table Processing Order:**
1. `metrics_metricsnapshot` (parent) — compacted first, excluding rows still referenced by children
2. `metrics_gpumetric`, `metrics_storagemetric`, `metrics_networkmetric` (children) — compacted after parent
3. `metrics_dockercontainermetric`, `metrics_gpu_process` — compacted if data exists

**FK Handling:**
- Parent table rows referenced by children are excluded from compaction (to avoid FK violations)
- Child tables keep their `snapshot_id` pointing to the parent for data integrity

**Options:**
| Flag | Description |
|---|---|
| `--dry-run` | Preview row counts without making changes |
| `--verbose` | Show per-table row counts and status |

**Example Output:**
```
Phase 1: Compacting 1-minute -> 1-hour buckets (data older than 1 day)
  metrics_metricsnapshot: 50,000 rows compacted
  metrics_gpumetric: 390,000 rows compacted
  metrics_storagemetric: 357,000 rows compacted
  metrics_networkmetric: 197,000 rows compacted
Compaction complete
```

---

### `cleanup_old_data` — Delete Data Older Than N Days

**Purpose:** Permanently removes data older than the retention period to free storage.

**How It Works:**

- Processes tables in dependency order (children first, parent last)
- For each table, deletes rows where `timestamp < cutoff` in batches of 10,000
- Batch deletion avoids long table locks that would block the live agent
- Uses `id` column for batch selection (except `metrics_latest_snapshot` which uses `rig_uuid` as PK)
- Skips tables without a `timestamp` column

**Table Processing Order:**
1. `metrics_gpu_process` (child of MetricSnapshot)
2. `metrics_gpumetric` (child of MetricSnapshot)
3. `metrics_storagemetric` (child of MetricSnapshot)
4. `metrics_networkmetric` (child of MetricSnapshot)
5. `metrics_dockercontainermetric` (child of MetricSnapshot)
6. `metrics_rig_status_event` (independent)
8. `metrics_metricsnapshot` (parent — deleted last so FK constraints are satisfied)
9. `metrics_latest_snapshot` (independent, uses `rig_uuid` as PK, no timestamp column)

**Options:**
| Flag | Description |
|---|---|
| `--days N` | Delete data older than N days (default: 31) |
| `--dry-run` | Preview row counts without making changes |
| `--verbose` | Show per-table row counts and status |

**Example Output:**
```
Cleaning up data older than 31 days (before 2026-05-09 02:18)
  metrics_gpu_process: nothing to delete
  metrics_gpumetric: nothing to delete
  metrics_storagemetric: nothing to delete
  metrics_networkmetric: nothing to delete
  metrics_dockercontainermetric: nothing to delete
  metrics_rig_status_event: nothing to delete
  metrics_metricsnapshot: nothing to delete
  metrics_latest_snapshot: 2 rows to delete
  metrics_latest_snapshot: deleted 2 rows
Total rows deleted: 2
```

---

## Scheduling

Both commands run sequentially via `data_retention.sh` wrapper, called by cron:

```bash
# /etc/cron.d/monitoring-data-cleanup
0 3 * * * qrv bash /opt/gpu_monitor/deploy/data_retention.sh >> /var/log/monitoring-agent/cleanup-cron.log 2>&1
```

The wrapper:
1. Activates the virtualenv and sources `.env`
2. Runs `compact_data --verbose` (single phase: 1-minute → 1-hour buckets)
3. Runs `cleanup_old_data --days=31` (uses default retention)
4. Logs all output to `/var/log/monitoring-agent/cleanup-cron.log`

---

## Verification

**Check if compaction is working:**
```bash
cd /opt/gpu_monitor
source venv/bin/activate && set -a && source .env && set +a

# See row counts and time ranges per table
python -c "
import os; os.environ['DJANGO_SETTINGS_MODULE'] = 'gpu_monitor.settings'
import django; django.setup()
from django.db import connection
for t in ['metrics_metricsnapshot', 'metrics_gpumetric', 'metrics_storagemetric',
          'metrics_networkmetric']:
    with connection.cursor() as c:
        c.execute(f'SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {t}')
        row = c.fetchone()
        print(f'{t}: {row[0]:,} rows, {row[1]} to {row[2]}')
"
```

**Check cron job status:**
```bash
cat /etc/cron.d/monitoring-data-cleanup
tail -f /var/log/monitoring-agent/cleanup-cron.log
```

**Manual run (if cron failed):**
```bash
cd /opt/gpu_monitor
source venv/bin/activate && set -a && source .env && set +a
python manage.py compact_data --verbose
python manage.py cleanup_old_data --days=31 --verbose
```

---

## Troubleshooting

### Compaction fails with FK violation
**Cause:** Parent table rows are referenced by child table rows.
**Fix:** The command handles this automatically — parent rows still referenced by children are excluded from compaction.

### Compaction fails with "relation already exists"
**Cause:** A previous run was interrupted, leaving a temp table behind.
**Fix:** The command now uses `DROP TABLE IF EXISTS` before creating temp tables. This is handled automatically.

### Cleanup fails with "column id does not exist"
**Cause:** `metrics_latest_snapshot` uses `rig_uuid` as primary key, not `id`.
**Fix:** The command detects this and uses the correct PK column per table.

### Database still growing after enabling retention
**Cause:** Cron job may not be running, or compaction hasn't caught up yet.
**Fix:** Check cron job status, run manually, and verify with the verification query above.
