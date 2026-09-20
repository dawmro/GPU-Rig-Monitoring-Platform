# 3-Tier Data Compaction Buckets — Implementation Plan

## Current State Analysis

### Current Implementation
**File:** `gpu_monitor/metrics_app/management/commands/compact_data.py`

**Single-phase compaction:**
- **Cutoff:** 1 day (24 hours)
- **Bucket size:** 60 minutes (1 hour)
- **Process:** All data older than 1 day → 1-hour buckets
- **Tables processed:** GPU, Storage, Network, PowerReading, MetricSnapshot
- **FK-safe:** Parent table compacted last with NOT EXISTS subqueries

**Configuration in code:**
```python
now = timezone.now()
cutoff = now - timedelta(days=1)  # Line 129
bucket_minutes = 60  # Line 136
```

### Current Documentation (DATA_RETENTION_PLAN.md)
- **Tier 1 (Raw):** 0-1 day, 1-minute buckets
- **Tier 2 (Compacted):** 1-31 days, 1-hour buckets
- **Tier 3 (Deleted):** 31+ days

---

## Proposed 3-Tier Compaction Strategy

| Tier | Age Range | Bucket Size | Purpose |
|------|-----------|-------------|---------|
| **Tier 1 (Raw)** | 0-1 day (0-24h) | 1-minute | Live Metrics, 24h charts |
| **Tier 2 (15-min)** | 1-7 days (24h-168h) | 15-minute | 7d charts with higher granularity |
| **Tier 3 (1-hour)** | 7-31 days (168h-744h) | 1-hour | 30d charts |
| **Tier 4 (Deleted)** | 31+ days | — | — |

### Benefits
- **7d charts**: 15-min buckets = 672 data points (vs 168 with 1-hr) → smoother trend lines
- **Storage impact**: Minimal increase (15-min vs 1-min = 4x compression vs 60x for 1-hr)
- **Backward compatible**: Existing 1-hour compacted data stays valid
- **Charts work naturally**: ChartDataView already uses 1-min for 24h, 1-hr for 7d/30d

---

## Implementation Plan

### Phase 1: Core Logic Changes (`compact_data.py`)

#### 1.1 Add Tier Configuration Constants
```python
TIER_1_CUTOFF = timedelta(days=1)      # 24 hours
TIER_2_CUTOFF = timedelta(days=7)      # 168 hours
TIER_3_CUTOFF = timedelta(days=31)     # 744 hours (existing)

TIER_2_BUCKET_MINUTES = 15             # 15 minutes
TIER_3_BUCKET_MINUTES = 60             # 1 hour
```

#### 1.2 Two-Phase Compaction Logic
**Phase A (Tier 2): 1-min → 15-min buckets for data 1-7 days old**
- Cutoff range: `TIER_1_CUTOFF` to `TIER_2_CUTOFF` (24h to 168h)
- Bucket size: 15 minutes
- Bucket expression: `date_trunc('hour', timestamp) + INTERVAL '15 min' * (EXTRACT(MINUTE FROM timestamp)::int / 15)`

**Phase B (Tier 3): 15-min → 1-hr buckets for data 7-31 days old**
- Cutoff range: `TIER_2_CUTOFF` to `TIER_3_CUTOFF` (168h to 744h)
- Bucket size: 60 minutes
- **Source data:** Can be either raw 1-min data (if Tier 2 not yet run) OR already-compacted 15-min data
- Must handle both cases gracefully

#### 1.3 Bucket Timestamp Calculation

**15-minute buckets:**
```sql
date_trunc('hour', timestamp) + INTERVAL '15 min' * (EXTRACT(MINUTE FROM timestamp)::int / 15)
-- Results in: HH:00, HH:15, HH:30, HH:45
```

**1-hour buckets:**
```sql
date_trunc('hour', timestamp)
-- Results in: HH:00
```

#### 1.4 Aggregation Functions (Per Tier)

**Tier 2 (15-min from 1-min raw):**
- Gauges (temp, util%, power, clocks): `AVG`
- Counters (network bytes, error_count, disk I/O deltas): `SUM`
- Static fields (model, UUIDs, capacities): `LAST` (ARRAY_AGG ORDER BY timestamp DESC)[1]

**Tier 3 (1-hour from 15-min compacted):**
- Same aggregation logic — works on already-aggregated data
- `AVG` of 15-min AVGs = correct hourly average
- `SUM` of 15-min SUMs = correct hourly total
- `LAST` of static fields = still correct

#### 1.5 Parent Table (MetricSnapshot) FK Handling
- Phase A: Exclude parent rows referenced by children in 1-7 day range
- Phase B: Exclude parent rows referenced by children in 7-31 day range
- Children already compacted in Phase A, so their FKs point to compacted rows

### Phase 2: Command Interface Updates

#### 2.1 New Arguments
```python
def add_arguments(self, parser):
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--phase', choices=['all', 'tier2', 'tier3'], default='all',
                        help='Run specific compaction phase')
    parser.add_argument('--days', type=int, default=31,
                        help='Retention period in days (default: 31)')
```

#### 2.2 Phase Control
```python
if options['phase'] in ('all', 'tier2'):
    self._compact_tier2(now, dry_run, verbose)
if options['phase'] in ('all', 'tier3'):
    self._compact_tier3(now, dry_run, verbose)
```

### Phase 3: ChartDataView Integration

**Current behavior (verified in `metrics_app/views.py`):**
```python
# Lines ~200-210 in ChartDataView
if range_hours <= 24:
    bucket_size = '1 minute'
elif range_hours <= 168:
    bucket_size = '1 hour'
else:
    bucket_size = '1 hour'
```

**Required change:** Update to use 15-minute for 7d:
```python
if range_hours <= 24:
    bucket_size = '1 minute'
elif range_hours <= 168:
    bucket_size = '15 minute'  # NEW: 7d charts
else:
    bucket_size = '1 hour'
```

**SQL aggregation must match:**
```python
if bucket_size == '15 minute':
    trunc = TruncMinute('timestamp')  # then group by 15-min intervals
    # or use custom bucket expression
```

### Phase 4: Documentation Updates

**Files to update:**
1. `docs/DATA_RETENTION_PLAN.md` — New tier table, space calculations
2. `docs/CHART_AGGREGATION_ANALYSIS.md` — Add 15-min aggregation rules
3. `docs/GPU_Rig_Monitoring_Architecture.md` — Update §8.5 retention section

---

## Detailed Code Changes

### compact_data.py — New Structure

```python
# Constants
TIER_1_CUTOFF_DAYS = 1      # Raw data retention
TIER_2_CUTOFF_DAYS = 7      # 15-min bucket retention
TIER_3_CUTOFF_DAYS = 31     # 1-hour bucket retention (deletion)

TIER_2_BUCKET_MINUTES = 15
TIER_3_BUCKET_MINUTES = 60

# Phase A: Tier 2 compaction (1-min → 15-min, 1-7 days old)
def _compact_tier2(self, now, dry_run, verbose):
    tier2_start = now - timedelta(days=TIER_2_CUTOFF_DAYS)  # 7 days ago
    tier2_end = now - timedelta(days=TIER_1_CUTOFF_DAYS)     # 1 day ago
    
    for config in COMPACT_TABLES:
        self._compact_table(
            config=config,
            window_start=tier2_start,
            window_end=tier2_end,
            bucket_minutes=TIER_2_BUCKET_MINUTES,
            dry_run=dry_run,
            verbose=verbose
        )

# Phase B: Tier 3 compaction (15-min → 1-hour, 7-31 days old)
def _compact_tier3(self, now, dry_run, verbose):
    tier3_start = now - timedelta(days=TIER_3_CUTOFF_DAYS)  # 31 days ago
    tier3_end = now - timedelta(days=TIER_2_CUTOFF_DAYS)     # 7 days ago
    
    for config in COMPACT_TABLES:
        self._compact_table(
            config=config,
            window_start=tier3_start,
            window_end=tier3_end,
            bucket_minutes=TIER_3_BUCKET_MINUTES,
            dry_run=dry_run,
            verbose=verbose
        )

# Enhanced _compact_table with explicit time window
def _compact_table(self, config, window_start, window_end, bucket_minutes, dry_run, verbose):
    """
    Compact data within [window_start, window_end) into buckets of bucket_minutes.
    """
    where_sql = "timestamp >= %s AND timestamp < %s {fk_where}"
    params = [window_start, window_end]
    # ... rest similar to current implementation
```

### Bucket Expression Helper

```python
def _bucket_expression(self, bucket_minutes):
    """Generate SQL bucket expression for given minute interval."""
    if bucket_minutes == 60:
        return "date_trunc('hour', timestamp)"
    elif bucket_minutes == 15:
        return ("date_trunc('hour', timestamp) + "
                "INTERVAL '15 min' * (EXTRACT(MINUTE FROM timestamp)::int / 15)")
    elif bucket_minutes == 1:
        return "date_trunc('minute', timestamp)"
    else:
        raise ValueError(f"Unsupported bucket size: {bucket_minutes}")
```

---

## Migration Strategy

### 1. Deploy new code with `--dry-run` first
- Run `compact_data --phase=tier2 --dry-run --verbose` to verify row counts
- Run `compact_data --phase=tier3 --dry-run --verbose` to verify

### 2. Backfill Tier 2 (15-min) for existing 1-7 day data
- Run `compact_data --phase=tier2 --verbose`
- This processes data currently in 1-min format that's 1-7 days old
- One-time operation, takes longer than daily runs

### 3. Tier 3 (1-hour) continues as before
- Existing 1-hour compacted data for 7-31 days is already correct
- Daily run will now use new 15-min → 1-hour logic for 7-day boundary

### 4. Verify charts work
- 24h chart: 1-min buckets (unchanged)
- 7d chart: 15-min buckets (NEW — was 1-hour)
- 30d chart: 1-hour buckets (unchanged)

---

## Storage Impact Estimation

**Current (1-min → 1-hour at 1 day):**
- Day 0-1: 1,440 rows/day × 15.7 MB/day = 15.7 MB
- Day 1-31: 24 rows/day × 0.26 MB/day × 30 = 7.8 MB
- **Total 31-day: ~23.5 MB/rig**

**Proposed (1-min → 15-min at 1 day, 15-min → 1-hour at 7 days):**
- Day 0-1: 1,440 rows/day = 15.7 MB
- Day 1-7: 96 rows/day (15-min) × 0.17 MB/day × 6 = 1.0 MB
- Day 7-31: 24 rows/day (1-hour) × 0.26 MB/day × 24 = 6.2 MB
- **Total 31-day: ~22.9 MB/rig** (slightly less due to better compression at 15-min)

**7-day chart improvement:** 672 points (15-min) vs 168 points (1-hour) = 4× resolution

---

## Testing Checklist

- [ ] `compact_data --phase=tier2 --dry-run --verbose` shows correct row counts
- [ ] `compact_data --phase=tier3 --dry-run --verbose` shows correct row counts
- [ ] 7-day chart renders with 15-min data points (672 points)
- [ ] 24h chart still uses 1-min data (1,440 points)
- [ ] 30d chart still uses 1-hour data (720 points)
- [ ] Parent table FK exclusion works for both phases
- [ ] Dry-run doesn't modify data
- [ ] Verbose output shows per-batch progress
- [ ] Cron job runs both phases daily

---

## Rollback Plan

If issues arise:
1. Revert `compact_data.py` to single-phase version
2. Existing 1-hour compacted data remains valid
3. 7d charts temporarily fall back to 1-hour buckets (ChartDataView change is forward-compatible)
4. No data loss — raw data (0-1 day) untouched