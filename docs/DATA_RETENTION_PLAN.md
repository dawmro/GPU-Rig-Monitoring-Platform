# Data Retention Analysis & Plan

## Current Database State

### Total Size: 90 MB
- Metric tables: 81 MB
- Other (users, sessions, audit, etc.): ~9 MB

### Table Breakdown (largest first)

| Table | Size | Rows | Avg rows/day/rig |
|---|---|---|---|
| ErrorEventOccurrence | 20 MB | 56,503 | — |
| LastErrors | 22 MB | 43,195 | — |
| MetricSnapshot | 13 MB | 14,000 | 285.7 |
| StorageMetric | 9.9 MB | 31,505 | 1,050.2 |
| GPUMetric | 9.5 MB | 24,318 | 1,519.9 |
| NetworkMetric | 7.3 MB | 20,651 | 688.4 |
| GPUProcessMetric | 216 kB | 47 | — |
| RigStatusEvent | 152 kB | 109 | — |

### Per-Rig Breakdown (MetricSnapshot rows)

| Rig | Rows | % of total |
|---|---|---|
| ubuntu-vm | 6,961 | 49.7% |
| win-10-rtx-3060 | 2,420 | 17.3% |
| ZET2559 | 1,879 | 13.4% |
| rig5090Gigabyte | 1,518 | 10.8% |
| ZET2558 | 824 | 5.9% |
| Unnamed Rig | 398 | 2.8% |

### Data Age
- MetricSnapshot: 7 days (Jun 1 → Jun 8)
- GPUMetric: 4 days (Jun 4 → Jun 8)
- 6 active rigs (7 total, 1 unnamed with minimal data)

---

## Projection for 1,000 Rigs

### Assumptions
- Current rigs have ~50% uptime (as stated)
- Agent sends data every 60 seconds when online
- At 50% uptime: ~720 rows/day/rig (not 1,440)
- GPUMetric: ~1,520 rows/day/rig at full uptime → ~760 at 50%
- StorageMetric: ~1,050 rows/day/rig at full uptime → ~525 at 50%
- NetworkMetric: ~688 rows/day/rig at full uptime → ~344 at 50%
- ErrorEventOccurrence: varies, assume ~50/day/rig at 50% uptime
- LastErrors: assume ~40/day/rig at 50% uptime

### Daily Insertion Rate (1,000 rigs at 50% uptime)

| Table | Rows/day/rig | × 1,000 rigs | Daily total |
|---|---|---|---|
| MetricSnapshot | 143 | × 1,000 | 143,000 |
| GPUMetric | 760 | × 1,000 | 760,000 |
| StorageMetric | 525 | × 1,000 | 525,000 |
| NetworkMetric | 344 | × 1,000 | 344,000 |
| ErrorEventOccurrence | 50 | × 1,000 | 50,000 |
| LastErrors | 40 | × 1,000 | 40,000 |
| **Total** | **1,862** | × 1,000 | **1,862,000** |

### Monthly Storage Estimate (30 days)

Current data density: ~81 MB / 143,000 total rows ≈ 0.57 KB/row (average across all metric tables)

At 1,000 rigs:
- Daily: 1,862,000 rows × 0.57 KB ≈ **1.06 GB/day**
- Monthly: 1.06 GB × 30 ≈ **31.8 GB/month**

### Storage by Retention Period

| Retention | Storage (1,000 rigs) | Notes |
|---|---|---|
| 7 days | ~7.4 GB | Minimum useful for charts |
| 14 days | ~14.9 GB | Good for weekly trends |
| 30 days | ~31.8 GB | Full month of data |
| 60 days | ~63.6 GB | Two months |
| 90 days | ~95.4 GB | Quarter |
| 180 days | ~190.8 GB | Half year |
| 365 days | ~387.2 GB | Full year |

---

## Retention Recommendation

### Recommended: 30 days

**Rationale:**
- Matches the maximum chart timeframe (30d Historical Charts)
- Provides full month of data for trend analysis
- ~32 GB/month is manageable for a VPS
- PostgreSQL handles this volume easily
- No need to keep data longer than the UI can display

### Alternative: 60 days

If storage allows (~64 GB/month), 60 days provides:
- Better long-term trend visibility
- Comparison between two months
- Still manageable on a VPS with 100+ GB storage

### Not recommended: > 90 days

- Storage grows quickly (~95+ GB/month)
- UI only shows up to 30 days
- Diminishing returns for analysis
- Better to archive to cold storage if needed

---

## Implementation Plan

### 1. Management Command: `cleanup_old_data`

Create a Django management command that deletes data older than N days:

```bash
python manage.py cleanup_old_data --days=30
```

### 2. Tables to Clean

| Table | Order | Notes |
|---|---|---|
| ErrorEventOccurrence | 1 | Delete by timestamp |
| LastErrors | 2 | Delete by timestamp |
| GPUMetric | 3 | Delete by timestamp |
| StorageMetric | 4 | Delete by timestamp |
| NetworkMetric | 5 | Delete by timestamp |
| GPUProcessMetric | 6 | Delete by timestamp (or keep latest only) |
| MetricSnapshot | 7 | Delete by timestamp |
| RigStatusEvent | 8 | Delete by timestamp |

### 3. Scheduling

Run daily via cron at a random time (e.g., 3-4 AM):

```bash
# /etc/cron.d/monitoring-cleanup
0 3 * * * root cd /opt/gpu_monitor && source venv/bin/activate && set -a && source .env && set +a && python manage.py cleanup_old_data --days=30 >> /var/log/monitoring-agent/cleanup.log 2>&1
```

### 4. Safety Features

- Dry-run mode: `--dry-run` flag to preview what would be deleted
- Batch deletion: Delete in chunks of 10,000 rows to avoid long locks
- Logging: Log number of rows deleted per table
- Error handling: Continue on single table failure

### 5. Edge Cases

| Case | Handling |
|---|---|
| No data older than N days | Log and exit cleanly |
| Very large deletion | Batch in 10k row chunks with COMMIT between |
| Foreign key constraints | Delete child tables first (GPUMetric before MetricSnapshot) |
| Concurrent agent writes | Use DELETE with WHERE timestamp < NOW() - interval, non-blocking |
| First run (lots of data) | May take longer, but cron runs at 3 AM so acceptable |

---

## Pros and Cons

### 30-Day Retention

**Pros:**
- Matches UI capabilities (30d charts)
- Predictable storage (~32 GB/month at 1,000 rigs)
- Simple to explain and manage
- PostgreSQL performs well at this scale

**Cons:**
- No historical comparison beyond 1 month
- If you need to investigate something from 2 months ago, data is gone

### 60-Day Retention

**Pros:**
- Two months of comparison data
- Still manageable storage (~64 GB/month)

**Cons:**
- 2× storage cost
- UI can't display it anyway (max 30d charts)

### No Retention (Keep Everything)

**Pros:**
- All historical data available
- No risk of deleting something needed

**Cons:**
- Storage grows indefinitely (~387 GB/year at 1,000 rigs)
- Database performance degrades over time
- Backup size grows
- Cost increases over time

---

## Recommendation

**Start with 30 days.** It matches the UI, is predictable, and manageable. Can always increase later if needed.

The management command should be created but NOT scheduled immediately — first verify it works correctly with `--dry-run`, then enable the cron job.
