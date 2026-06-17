# Data Retention — Implementation Reference

## Measured Database Usage

### Current State (100 rigs, 10 days)
| Table | Size | Live Rows | Dead Rows | Bytes/Row |
|-------|------|----------|-----------|-----------|
| metrics_gpumetric | 7,584 MB | 5,495,005 | 160 | 1,447 |
| metrics_metricsnapshot | 2,899 MB | 1,032,973 | 0 | 2,943 |
|| metrics_dockercontainermetric | ~~2,333 MB~~ | ~~2,414,960~~ | ~~0~~ | ~~1,013~~ |
| metrics_storagemetric | 1,964 MB | 2,377,947 | 0 | 866 |
| metrics_networkmetric | 931 MB | 884,129 | 0 | 1,104 |
| metrics_gpu_process | 1,200 kB | 748 | 748 | 821 |
| **Total metric tables** | **15,714 MB (15.3 GB)** | | | |

### Per-Rig Storage (100% uptime, measured)
| Period | Storage |
|--------|---------|
| 1 day | 15.7 MB |
| 10 days | 157 MB |
| 30 days | 471 MB |
| 31 days | 487 MB |

**Note:** The earlier projection of ~4.7 MB/day/rig was based on estimates before the platform was fully deployed with real agents. The actual measured storage is ~3.3x higher due to:
- Larger snapshot rows (2,943 B vs estimated ~1,500 B) from JSON fields (motherboard_json, software_json, cpu_load_avg_json)
- Docker container metrics being more prolific than expected (2,414,960 rows for 100 rigs over 10 days)

### Average Rig Configuration (measured)
- **GPUs per rig:** ~5.3 (varies by rig)
- **Disks per rig:** ~2.3
- **Network interfaces per rig:** ~0.9
- **Docker containers per rig:** ~2.3
- **Snapshots per rig per day:** ~1,033 (not full 1440 due to uptime < 100%)

### Projected Storage for 1,000 Rigs
| Retention | Raw Storage | After Compaction |
|-----------|-------------|------------------|
| 1 day | 15.3 GB | 15.3 GB |
| 7 days | 107 GB | 22 GB |
| 31 days | 460 GB | 72 GB |

### Projected Storage for 10,000 Rigs
| Retention | Raw Storage | After Compaction |
|-----------|-------------|------------------|
| 1 day | 153 GB | 153 GB |
| 7 days | 1.07 TB | 220 GB |
| 31 days | 4.6 TB | 720 GB |

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
31 days × 15.7 MB/day × 1,000 rigs = **486.7 GB**

### With Tiered Compaction

| Tier | Period | Raw Size | Factor | Compact Size |
|---|---|---|---|---|
| Raw | Day 0-1 | 15.3 GB | 1× | 15.3 GB |
| 1-hour | Day 1-31 | 471.4 GB | 60× | 7.9 GB |
| **Total** | **31 days** | **486.7 GB** | | **~23.2 GB** |

**~95% storage reduction** through compaction (486.7 GB → 23.2 GB).

**Per-rig 31-day retention:** 15.7 MB (raw day) + 7.9 MB (30 days compacted) = **23.6 MB**

---

## Management Commands

### `compact_data` — Aggregate Old Data into Larger Buckets

**Purpose:** Reduces storage by aggregating per-minute rows into 1-hour buckets.

**How It Works:**

Single phase:

**Phase 1** (data > 1 day old):
- Creates 1-hour buckets from per-minute data
- Groups rows by `rig_uuid` (and `gpu_index`, `device`, etc.) into 1-hour windows
|- Applies aggregation per metric type:
  - `AVG` for gauges: temperature, utilization, power, memory usage, GPU core/memory clock, utilization_pct
  - `SUM` for counters: network byte deltas, error_count, read_bytes_delta, write_bytes_delta, read_iops_delta, write_iops_delta
  - `LAST` for static fields: model names, UUIDs, capacity, PCIe link info, read_bytes, write_bytes, read_iops, write_iops, busy_time_ms
  - Note: Disk I/O cumulative counters (read_bytes, write_bytes, read_iops, write_iops) use LAST — the latest cumulative value in the bucket
  - Note: Disk I/O deltas use SUM — total bytes/IOPS transferred during the bucket period
- Deletes original per-minute rows, inserts aggregated rows
- FK-safe: parent rows referenced by children are excluded from compaction

**Table Processing Order:**
1. `metrics_gpu_process` (child with FK) — compacted first
2. `metrics_gpumetric` (child with FK) — compacted after gpu_process
3. `metrics_storagemetric` (child with FK) — compacted after gpu
4. `metrics_networkmetric` (child with FK) — compacted after storage
5. `metrics_metricsnapshot` (parent) — compacted LAST, excluding rows still referenced by children

**FK Handling:**
- Parent table rows referenced by children are excluded from compaction (to avoid FK violations)
- Child tables (GPU, storage, network, gpu_process) keep their `snapshot_id` pointing to the parent
- `metrics_latest_docker_container` is not compacted (delete-before-insert, latest only)

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

## Old Data Cleanup

Deletes metric data older than configurable retention period (default: 31 days).
1. `metrics_gpu_process` (child of MetricSnapshot)
2. `metrics_gpumetric` (child of MetricSnapshot)
3. `metrics_storagemetric` (child of MetricSnapshot)
4. `metrics_networkmetric` (child of MetricSnapshot)
5. `metrics_latest_docker_container` (independent — latest snapshot, delete-before-insert)
6. `metrics_rig_status_event` (independent)
7. `metrics_metricsnapshot` (parent — deleted last so FK constraints are satisfied)
8. `metrics_latest_snapshot` (independent, uses `rig_uuid` as PK, no timestamp column)

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
  metrics_latest_docker_container: skipped (no timestamp)
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
