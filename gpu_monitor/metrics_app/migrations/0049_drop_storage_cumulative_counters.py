"""Drop cumulative I/O counter columns from StorageMetric.

Background
----------
StorageMetric historically stored BOTH cumulative counters
(read_bytes, write_bytes, read_iops, write_iops, busy_time_ms) AND
the pre-computed per-interval deltas (read_bytes_delta, etc.).

The cumulative counters were only used by:
1. The serializer's delta calculation — but the serializer actually
   reads cumulative values from LatestSnapshot.storage_*_total_json,
   not from the previous StorageMetric row.
2. compact_data's `'last'` aggregation — to preserve the latest
   cumulative value through tier compaction. This is a self-referencing
   use: the next tier reads the previous tier's `last` value.

What actually reads from StorageMetric
---------------------------------------
- chart view: only `*_delta` and `utilization_pct` columns
- report endpoint: only `*_delta` and `utilization_pct` columns
- Live Metrics view: only `LatestSnapshot.*_delta_json`

What was being written to StorageMetric
----------------------------------------
Per heartbeat, per disk, 5 cumulative + 4 delta + 1 utilization columns.
After this migration, only the 4 delta + 1 utilization columns are
written, saving 5 × 8 bytes (40 bytes) per row.

Storage savings (current DB)
-----------------------------
21,860 rows × 5 cumulative columns × 8 bytes/column = 874 KB
saved immediately. Compaction will continue to reduce this as
older rows are aggregated, but the schema is permanently leaner.

For a 4-disk rig at 60s heartbeat:
- Per heartbeat: saves 5 × 8 = 40 bytes × 4 = 160 bytes
- Per day: 160 × 60 × 24 = 230 KB/rig/day
- For 100 rigs: 23 MB/day of cumulative I/O counter writes eliminated

Where the cumulative values still live
---------------------------------------
- LatestSnapshot.storage_read_bytes_total_json  (per device, per rig)
- LatestSnapshot.storage_write_bytes_total_json
- LatestSnapshot.storage_read_iops_total_json
- LatestSnapshot.storage_write_iops_total_json
- LatestSnapshot.storage_busy_time_ms_total_json

These are the canonical source for cumulative disk I/O and are
read by the serializer to compute deltas on the next ingest.

Backward compatibility
-----------------------
- No agent code change required — the agent still sends cumulative
  counters in the payload, and the serializer still stores them in
  LatestSnapshot.
- No chart or report query change required — they never read
  cumulative values from StorageMetric.
- Migration is a column drop on an existing table; it is safe to
  run on a production database (DROP COLUMN in PG is non-blocking
  on PG ≥ 11; this project requires PG ≥ 12).

Removed columns
---------------
- read_bytes (BIGINT)
- write_bytes (BIGINT)
- read_iops (INTEGER)
- write_iops (INTEGER)
- busy_time_ms (INTEGER)
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('metrics_app', '0048_drop_power_reading_table'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='storagemetric',
            name='busy_time_ms',
        ),
        migrations.RemoveField(
            model_name='storagemetric',
            name='read_bytes',
        ),
        migrations.RemoveField(
            model_name='storagemetric',
            name='read_iops',
        ),
        migrations.RemoveField(
            model_name='storagemetric',
            name='write_bytes',
        ),
        migrations.RemoveField(
            model_name='storagemetric',
            name='write_iops',
        ),
    ]
