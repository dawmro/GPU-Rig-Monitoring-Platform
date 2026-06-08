# Data Retention Plan

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

### Tier 2: 15-Minute Buckets (1-7 days)
- Compact data older than 1 day into 15-minute buckets
- Reduce 1,440 rows/day to 96 rows/day (15× savings)
- 7d charts use 15-minute buckets anyway

### Tier 3: 1-Hour Buckets (7-31 days)
- Compact data older than 7 days into 1-hour buckets
- Reduce 1,440 rows/day to 24 rows/day (60× savings)
- 30d charts use 1-hour buckets anyway

### Tier 4: Delete (31+ days)
- Remove all data older than 31 days
- 31 days provides 1-day safety margin beyond the 30-day max chart range

---

## Space Savings Calculation

### Without Compaction
31 days × 4.7 MB/day × 1,000 rigs = **145.7 GB**

### With Tiered Compaction

| Tier | Period | RawSize | Factor | CompactSize |
|---|---|---|---|---|
| Raw | Day 0-1 | 4.7 GB | 1× | 4.7 GB |
| 15-min | Day 1-7 | 32.9 GB | 15× | 2.2 GB |
| 1-hour | Day 7-31 | 112.8 GB | 60× | 1.9 GB |
| **Total** | **31 days** | **150.4 GB** | | **~9 GB** |

**94% storage reduction** through compaction.

---

## Implementation

### Management Commands

1. `compact_data` — Aggregate old data into larger buckets
2. `cleanup_old_data` — Delete data older than 31 days

### Schedule
Both run sequentially via daily cron at 3 AM.

---

## Pros and Cons

### 31-Day Retention with Compaction

**Pros:**
- Matches UI max timeframe (30d charts) + 1 day safety margin
- ~9 GB/month at 1,000 rigs (very manageable)
- Keeps full-resolution data for 1 day (live metrics)
- Keeps 15-min data for 7 days (weekly trends)
- Keeps 1-hour data for 31 days (monthly trends)

**Cons:**
- Data older than 31 days is permanently lost
- Compaction logic adds complexity
- Need to maintain aggregation queries
