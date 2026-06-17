# PostgreSQL VACUUM FULL ANALYZE — Analysis for GPU Rig Monitoring Platform

## Question

After running `compact_data` and `cleanup_old_data` scripts via cron once a day,
should we also run `VACUUM FULL ANALYZE`?

## How PostgreSQL VACUUM Works

### Regular VACUUM (without FULL)
- Reclaims space occupied by dead tuples (deleted/updated rows)
- Makes space available for **reuse within the same table**
- Does NOT release space back to the OS
- Runs concurrently with reads/writes — **no exclusive lock**
- PostgreSQL's `autovacuum` daemon does this automatically

### VACUUM FULL ANALYZE
- Physically rewrites the entire table into a new file
- Releases unused space **back to the operating system**
- **Acquires an exclusive lock** on the table for the entire duration
- Blocks all reads and writes to the table while running
- Also runs `ANALYZE` (updates query planner statistics)

### ANALYZE (alone)
- Updates statistics used by the query planner
- Determines most efficient query execution plans
- Very fast, minimal overhead, no locks

## What Our Scripts Do

### compact_data.py
- Aggregates 1-minute rows into 1-hour buckets for data older than 1 day
- Deletes original rows after aggregation (produces dead tuples)
- Runs `DELETE` + `INSERT` per batch (generates WAL and dead tuples)

### cleanup_old_data.py
- Deletes rows older than 31 days in batches of 10,000
- Deletes produce dead tuples until autovacuum reclaims them
- FK-safe ordering (children first, parent last)

### What happens after these scripts run?
1. Many dead tuples are created (from DELETE operations)
2. Autovacuum eventually reclaims them, but may lag behind
3. Table can grow larger than necessary (bloat)
4. Query planner statistics become stale

## Pros of VACUUM FULL ANALYZE

1. **Reclaims OS-level disk space** — tables shrink physically
2. **Removes table bloat** — especially important after bulk DELETEs
3. **Updates planner statistics** — better query plans after data distribution changes
4. **Defragements tables** — sequential reads faster after rewrite
5. **Predictable maintenance window** — runs when we schedule it, not when autovacuum gets around to it

## Cons of VACUUM FULL ANALYZE

1. **Exclusive lock = downtime** — tables are completely blocked during operation
   - For large tables (GPUMetric with millions of rows), this could take minutes
   - During this time, the agent CANNOT ingest data → missed heartbeats
2. **Space temporarily doubles** — VACUUM FULL creates a new copy of the table
   - Need 2x the current database size in free disk space
   - A 50GB database needs 50GB free to run VACUUM FULL
3. **WAL (Write-Ahead Log) spike** — generates massive WAL traffic
   - Can fill up WAL disk if not monitored
   - Slows down the entire database during rewrite
4. **Not needed frequently** — PostgreSQL's autovacuum handles regular dead tuple cleanup well
5. **Risk on production table** — if something goes wrong during VACUUM FULL, it can leave the table in an inconsistent state (rare but possible)

## Recommended Approach: VACUUM ANALYZE (without FULL)

### Why not VACUUM FULL
The exclusive lock is the dealbreaker. Our monitoring platform runs agent ingest every 30 seconds. A VACUUM FULL on the GPUMetric table could block ingest for 30+ seconds, causing data loss and false "stale" alarms.

### Why VACUUM ANALYZE (regular, no FULL) IS beneficial

After daily `compact_data` + `cleanup_old_data`:
1. Many dead tuples exist temporarily until autovacuum processes them
2. Query planner statistics are stale (data distribution changed significantly)
3. A regular `VACUUM ANALYZE` can:
   - Reclaim dead tuple space (without OS-level compaction)
   - Update planner statistics immediately
   - Run concurrently with production traffic (no exclusive lock)
   - Be much faster than VACUUM FULL

### Even better: target specific tables

Instead of `VACUUM FULL ANALYZE;` (whole database), run:
```sql
-- After compact_data and cleanup_old_data, analyze only the affected tables
VACUUM ANALYZE metrics_gpumetric;
VACUUM ANALYZE metrics_storagemetric;
VACUUM ANALYZE metrics_networkmetric;
VACUUM ANALYZE metrics_gpu_process;
VACUUM ANALYZE metrics_metricsnapshot;
```

This:
- Only locks our actual data tables (not the whole database)
- Runs concurrently (no blocking)
- Updates statistics for query planner
- Reclaims dead tuple space for reuse
- Takes seconds instead of minutes

## pg_repack Alternative (Advanced)

For cases where OS-level space reclaim is truly needed:
- `pg_repack` is a third-party tool that compacts tables WITHOUT exclusive locks
- Works by creating a shadow table, copying data, then swapping
- Install: `apt install postgresql-16-repack` (or build from source)
- Usage: `pg_repack -d gpu_monitor -t metrics_gpumetric`
- Pros: No downtime, reclaims OS space
- Cons: Requires installation, more complex, higher resource usage during repack

## Recommendation

**DO NOT use `VACUUM FULL ANALYZE`** on this database. The exclusive lock risk is too high for a production monitoring system.

**DO use targeted `VACUUM ANALYZE`** on affected tables after daily maintenance:

```bash
# Add to daily maintenance cron (after compact_data and cleanup_old_data)
sudo -u postgres psql -d gpu_monitor -c "
  VACUUM ANALYZE metrics_gpumetric;
  VACUUM ANALYZE metrics_storagemetric;
  VACUUM ANALYZE metrics_networkmetric;
  VACUUM ANALYZE metrics_gpu_process;
  VACUUM ANALYZE metrics_metricsnapshot;
"
```

**Schedule:** Daily, during off-peak hours (e.g., 3:00 AM), AFTER compact_data and cleanup_old_data run.

**Monitor:** Check `pg_stat_user_tables` for dead tuple counts and last vacuum time:
```sql
SELECT relname, n_dead_tup, last_vacuum, last_autovacuum
FROM pg_stat_user_tables WHERE schemaname = 'public'
ORDER BY n_dead_tup DESC;
```
