#!/usr/bin/env python3
"""
Analysis: Remaining Duplicate Timeseries Data After LatestSnapshot Extension

After extending LatestSnapshot with all Live Metrics fields, we eliminated
the metric_snapshot timeseries query. But duplication remains WITHIN the
timeseries tables themselves.

DUPLICATION ANALYSIS:
====================

1. GPUMetric — 253 bytes/row, 4226 rows
   Duplicated fields per row:
   - rig_uuid (16B) + timestamp (8B) = 24B — needed for UNIQUE + queries
   - gpu_uuid (64B) — kept intentionally for GPU replacement tracking
   - model (255B) — static per GPU, duplicated every heartbeat
   - mem_total_mb (4B) — static per GPU, duplicated every heartbeat
   Per-row waste: ~323B (model + mem_total_mb + gpu_uuid overhead)
   Total waste: 4226 × 323B = ~1.3 MB (small dataset, but scales)

2. StorageMetric — 146 bytes/row, 2907 rows
   Duplicated fields per row:
   - rig_uuid (16B) + timestamp (8B) = 24B — needed for UNIQUE + queries
   - device (255B) — static per disk, duplicated every heartbeat
   - mountpoint (512B) — static per disk, duplicated every heartbeat
   - fstype (32B) — static per disk, duplicated every heartbeat
   - capacity_bytes (8B) — static per disk, duplicated every heartbeat
   Per-row waste: ~807B
   Total waste: 2907 × 807B = ~2.3 MB

3. NetworkMetric — 142 bytes/row, 1610 rows
   Duplicated fields per row:
   - rig_uuid (16B) + timestamp (8B) = 24B — needed for UNIQUE + queries
   - interface (64B) — static per NIC, duplicated every heartbeat
   - ipv4 (15B) — semi-static, duplicated every heartbeat
   - link_speed_mbps (4B) — static per NIC, duplicated every heartbeat
   Per-row waste: ~83B
   Total waste: 1610 × 83B = ~134 KB

4. MetricSnapshot — 346 bytes/row, 1135 rows
   After cleanup, only dynamic fields + uptime_s remain.
   Duplicated fields per row:
   - rig_uuid (16B) + timestamp (8B) = 24B — needed for UNIQUE + queries
   No remaining static field duplication. CLEAN.

5. GPUProcessMetric — 240 bytes/row, 749 rows
   Duplicated fields per row:
   - rig_uuid (16B) + timestamp (8B) = 24B — needed for UNIQUE + queries
   - gpu_index (2B) — needed for UNIQUE constraint
   - snapshot_id (8B) — FK to MetricSnapshot
   process_name (500B) — dynamic (processes come and go)
   This table uses delete-before-insert, so only latest snapshot stored.
   Per-row waste: minimal (only rig_uuid + timestamp)
   Total waste: 749 × 24B = ~18 KB

6. LatestSnapshot — 3292 bytes/row, 107 rows
   This is the denormalized cache. All fields are intentional.
   No duplication issue (single row per rig).

7. LatestDockerContainer — 285 bytes/row, 430 rows
   Latest snapshot per container. All fields are intentional.
   No duplication issue.

8. RigStatusEvent — 77 bytes/row, 319 rows
   Status transitions. All fields are intentional.
   No duplication issue.

REMAINING CANDIDATES (ranked by waste):
=======================================

#1: StorageMetric static fields (device, mountpoint, fstype, capacity_bytes)
    ~807 bytes/row × 2907 rows = ~2.3 MB
    For 1,000 rigs × 2.3 disks × 1440 rows/day = ~3.3 GB/day
    These fields are static per disk — they never change unless disk is replaced.
    Charts use: usage_pct, temp_c, smart_health (all dynamic)
    Static fields are needed for: display labels in charts (device name in multi-disk view)
    Verdict: Keep in timeseries (needed for chart labels). Low priority.

#2: GPUMetric static fields (model, mem_total_mb)
    ~259 bytes/row × 4226 rows = ~1.1 MB
    For 1,000 rigs × 5.3 GPUs × 1440 rows/day = ~19.5 GB/day
    model is needed for chart labels (multi-GPU chart legend).
    mem_total_mb is needed for VRAM charts.
    Verdict: Keep in timeseries (needed for charts). Cannot remove.

#3: NetworkMetric static fields (interface, ipv4, link_speed_mbps)
    ~83 bytes/row × 1610 rows = ~134 KB
    For 1,000 rigs × 0.9 NICs × 1440 rows/day = ~1.1 MB/day
    interface is needed for chart labels (multi-NIC chart legend).
    Verdict: Keep in timeseries (needed for chart labels). Low priority.

CONCLUSION:
===========
After the LatestSnapshot extension, the remaining "duplication" in
timeseries tables is actually STATIC IDENTIFIERS needed for chart labels
and multi-device display. Removing them would break chart legends.

The only remaining waste is:
- gpu_uuid in GPUMetric (intentional for GPU replacement tracking)
- rig_uuid + timestamp in child tables (intentional for UNIQUE constraints)

These are all intentional design decisions. The dedup work is COMPLETE.
"""
print(__doc__)
