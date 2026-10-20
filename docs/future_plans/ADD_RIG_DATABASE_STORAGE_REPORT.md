# Plan: Add Database Storage Per Rig to Report Tab

## Objective
Add a "Database Storage" section to the Report tab showing how much disk space (in MB) each rig's data occupies in the PostgreSQL database.

## Current State

### Data Models (tables storing per-rig data)
| Table | Model | Rows per Rig (1-min interval, 24h) | Rows per Rig (7d) | Rows per Rig (30d) |
|-------|-------|-----------------------------------|-------------------|-------------------|
| `metrics_metricsnapshot` | MetricSnapshot | ~1,440 | ~10,080 | ~43,200 |
| `metrics_gpumetric` | GPUMetric | ~1,440 × GPU_count | ~10,080 × GPU_count | ~43,200 × GPU_count |
| `metrics_storagemetric` | StorageMetric | ~1,440 × disk_count | ~10,080 × disk_count | ~43,200 × disk_count |
| `metrics_networkmetric` | NetworkMetric | ~1,440 × iface_count | ~10,080 × iface_count | ~43,200 × iface_count |
| `metrics_latest_snapshot` | LatestSnapshot | 1 row | 1 row | 1 row |
| `metrics_latest_docker_container` | LatestDockerContainer | N containers | N containers | N containers |
| `metrics_gpuprocessmetric` | GPUProcessMetric | (removed in migration 0047) | - | - |

### Data Retention & Compaction (Critical Context)
**The database storage per rig is the SAME for all report ranges (24h, 7d, 30d).**

The compaction/cleanup happens at the database level on a schedule:
- **Tier 1 (Raw)**: 0-1 day → 1-minute buckets
- **Tier 2 (15-min)**: 1-7 days → 15-minute buckets  
- **Tier 3 (1-hour)**: 7-31 days → 1-hour buckets
- **Cleanup**: Data >31 days deleted

So a rig's total database footprint includes:
- 1 day of raw 1-min data
- 6 days of 15-min buckets (4× smaller than raw)
- 23 days of 1-hour buckets (60× smaller than raw)
- LatestSnapshot (1 row) + Docker containers (few rows)

The report range selector (24h/7d/30d) only controls **what data is queried and displayed** — it does not affect the actual database storage.

### Existing Size Query Pattern (from `daily_maintenance.py`)
```sql
SELECT s.relname, s.n_live_tup, s.n_dead_tup,
       ROUND(pg_total_relation_size(c.oid) / 1024.0 / 1024.0, 1) AS total_mb
FROM pg_stat_user_tables s
JOIN pg_class c ON c.relname = s.relname
WHERE s.schemaname = 'public'
  AND s.relname IN %s
ORDER BY pg_total_relation_size(c.oid) DESC
```

This gives **total table size**, not per-rig size.

## Approach Options

### Option A: Row Count × Average Row Size (Recommended)
1. Get total table size and total row count per table
2. Calculate average row size = total_size / total_rows
3. Count rows per rig in each table
4. Estimate rig size = rig_row_count × avg_row_size

**Pros:** Fast (uses existing pg_stat stats), no full table scan
**Cons:** Estimate only, assumes uniform row size

### Option B: Per-Rig pg_column_size Sum (Accurate but Slow)
```sql
SELECT SUM(pg_column_size(t.*)) FROM metrics_metricsnapshot t WHERE rig_uuid = '...'
```

**Pros:** Accurate
**Cons:** Full table scan per rig — too slow for report endpoint

### Option C: pg_class.reltuples + pg_total_relation_size (PostgreSQL Stats)
Use `pg_class.reltuples` (estimated row count) + `pg_total_relation_size` + per-rig row count from index.

**Pros:** Uses planner stats
**Cons:** `reltuples` is estimate, may be stale

---

## Recommended Implementation: Option A (Row Count × Avg Row Size)

### 1. New Utility Function in `dashboard/views.py`

```python
def _get_rig_database_storage_mb(uuid_str):
    """Estimate database storage per rig in MB across all metric tables."""
    from django.db import connection
    
    tables = [
        'metrics_metricsnapshot',
        'metrics_gpumetric', 
        'metrics_storagemetric',
        'metrics_networkmetric',
        'metrics_latest_snapshot',
        'metrics_latest_docker_container',
    ]
    
    with connection.cursor() as cursor:
        # Get total table sizes and row counts
        cursor.execute("""
            SELECT s.relname, 
                   pg_total_relation_size(c.oid) as total_bytes,
                   s.n_live_tup as total_rows
            FROM pg_stat_user_tables s
            JOIN pg_class c ON c.relname = s.relname
            WHERE s.schemaname = 'public'
              AND s.relname = ANY(%s)
        """, [tables])
        table_stats = {row[0]: {'total_bytes': row[1], 'total_rows': row[2]} for row in cursor.fetchall()}
        
        # Get row count per rig for each table
        rig_sizes = {}
        for table in tables:
            stats = table_stats.get(table)
            if not stats or stats['total_rows'] == 0:
                rig_sizes[table] = 0
                continue
            
            avg_row_bytes = stats['total_bytes'] / stats['total_rows']
            
            # Count rig's rows using index (fast)
            cursor.execute(f"""
                SELECT COUNT(*) FROM {table} WHERE rig_uuid = %s
            """, [uuid_str])
            rig_row_count = cursor.fetchone()[0]
            
            rig_sizes[table] = round((rig_row_count * avg_row_bytes) / (1024 * 1024), 2)
        
        # Add latest snapshot tables (always 1 row or few rows)
        # These are small, include in total
        total_mb = sum(rig_sizes.values())
        return {
            'breakdown_mb': rig_sizes,
            'total_mb': round(total_mb, 2)
        }
```

### 2. Integrate into `_build_report_context`
Add call to `_get_rig_database_storage_mb(uuid_str)` and add result to context dict.

### 3. Add Template Section in `_report_table.html`
New section after "System" section showing:
- Total DB Storage: X.XX MB
- Breakdown table: Table name | Rows | Est. MB

---

## File Changes

| File | Change |
|------|--------|
| `gpu_monitor/dashboard/views.py` | Add `_get_rig_database_storage_mb()` function; call in `_build_report_context()` |
| `gpu_monitor/templates/dashboard/_report_table.html` | Add "Database Storage" section with total + breakdown table |

---

## Performance Considerations

- **Queries**: 1 query for table stats + 6 COUNT queries (one per table) = 7 queries
- **Indexes**: All tables have `(rig_uuid, -timestamp)` index → COUNT is index-only scan (fast)
- **Caching**: Report context cached 55s → size query runs once per cache period
- **TTL**: 55s aligns with heartbeat interval

---

## Example Output

```
Database Storage
┌─────────────────────────────┬───────┬──────────┐
│ Table                       │ Rows  │ Est. MB  │
├─────────────────────────────┼───────┼──────────┤
│ metrics_metricsnapshot      │ 1,440 │ 2.3 MB   │
│ metrics_gpumetric (×4 GPU)  │ 5,760 │ 8.7 MB   │
│ metrics_storagemetric (×2)  │ 2,880 │ 3.1 MB   │
│ metrics_networkmetric (×3)  │ 4,320 │ 1.8 MB   │
│ metrics_latest_snapshot     │ 1     │ 0.1 MB   │
│ metrics_latest_docker (×5)  │ 5     │ 0.1 MB   │
├─────────────────────────────┼───────┼──────────┤
│ Total                       │ 14,406│ 16.1 MB  │
└─────────────────────────────┴───────┴──────────┘
```

---

## Range Compatibility (24h / 7d / 30d)

**✅ Works identically for all three ranges** — the database storage per rig is a property of the rig's data in the database, not the report range. The compaction/cleanup process maintains the data at the database level on a fixed schedule (daily cron), independent of what range the user selects in the UI.

The report range selector only filters which time window of data is **queried and displayed** in the report tables/charts. The underlying database storage footprint remains constant.

---

## Edge Cases

1. **New rig (no data yet)**: All counts = 0, show 0 MB
2. **Table not in pg_stat**: Skip gracefully (table may not exist yet)
3. **Zero total rows**: Avoid division by zero
4. **Partitioned tables** (future): Would need adjustment (not currently used)

---

## Testing

1. Verify query works on dev database
2. Check report tab loads without timeout
3. Compare estimated size with `pg_total_relation_size` for validation
4. Test with multiple rigs (different GPU/disk/interface counts)
5. Verify same output for 24h, 7d, and 30d ranges