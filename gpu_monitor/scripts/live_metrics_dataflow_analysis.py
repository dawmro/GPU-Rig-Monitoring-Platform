#!/usr/bin/env python3
"""
Re-analysis: What data does Live Metrics actually need from MetricSnapshot?

The Live Metrics tab polls every 30s. The template _metrics_cards.html reads
from TWO data sources:

  1. snapshot (LatestSnapshot) — used for GPU, Storage, Network (already optimized)
  2. metric_snapshot (latest MetricSnapshot row) — used for CPU, Memory, Motherboard, Software

The metric_snapshot query (dashboard/views.py line 163-165) is:
    MetricSnapshot.objects.filter(rig_uuid=...).order_by('-timestamp').first()

This is a timeseries table query — the one remaining performance issue.

TEMPLATE READS FROM metric_snapshot:
====================================
CPU card:
  metric_snapshot.cpu_model              — STATIC (can change on CPU swap)
  metric_snapshot.cpu_physical_cores     — STATIC (can change on CPU swap)
  metric_snapshot.cpu_logical_cores      — STATIC (can change on CPU swap)
  metric_snapshot.cpu_utilization_pct   — DYNAMIC (changes every heartbeat)
  metric_snapshot.cpu_temp_c            — DYNAMIC
  metric_snapshot.cpu_load_avg_json     — DYNAMIC

Memory card:
  metric_snapshot.mem_total_bytes        — STATIC (can change on RAM upgrade)
  metric_snapshot.mem_used_bytes         — DYNAMIC
  metric_snapshot.mem_free_bytes         — DYNAMIC
  metric_snapshot.mem_cached_bytes       — DYNAMIC
  metric_snapshot.swap_total_bytes       — STATIC
  metric_snapshot.swap_used_bytes        — DYNAMIC

Motherboard card:
  metric_snapshot.motherboard_json       — STATIC (can change on mobo swap)

Software card:
  metric_snapshot.software_json.hostname     — STATIC
  metric_snapshot.software_json.os_distro    — STATIC
  metric_snapshot.software_json.kernel       — STATIC (can change on kernel update)
  metric_snapshot.software_json.uptime_s     — DYNAMIC
  metric_snapshot.software_json.nvidia_driver — STATIC (can change on driver update)
  metric_snapshot.software_json.docker_version — STATIC
  metric_snapshot.agent_version              — SEMI-STATIC

KEY INSIGHT: The template reads BOTH static and dynamic fields from the same
metric_snapshot object. We CANNOT remove the metric_snapshot query entirely
because cpu_utilization_pct, cpu_temp_c, mem_used_bytes etc. are DYNAMIC and
come from the timeseries table.

WHAT WE CAN DO:
===============

Option A: Add static fields to Rig, keep metric_snapshot query for dynamic only
  - Move cpu_model, cpu_physical_cores, cpu_logical_cores, mem_total_bytes,
    swap_total_bytes, motherboard_json, software_json to Rig model
  - Update Rig on every heartbeat (same pattern as latest_errors_json)
  - Template reads static fields from Rig, dynamic from metric_snapshot
  - metric_snapshot query still needed for CPU util, temp, load, memory usage
  - Savings: eliminates duplication of static data in MetricSnapshot
  - But: metric_snapshot query still hits timeseries table (just fewer fields)

Option B: Add ALL needed fields to LatestSnapshot, eliminate metric_snapshot query
  - Add cpu_model, cpu_physical_cores, cpu_logical_cores, cpu_utilization_pct,
    cpu_temp_c, cpu_load_avg_json, mem_total_bytes, mem_used_bytes, mem_free_bytes,
    mem_cached_bytes, swap_total_bytes, swap_used_bytes, motherboard_json,
    software_json, agent_version to LatestSnapshot
  - Template reads everything from snapshot (LatestSnapshot)
  - Eliminates the metric_snapshot query entirely
  - This is the SAME pattern already used for GPU/storage/network
  - Savings: eliminates both the static data duplication AND the extra query

Option C: Hybrid — static to Rig, dynamic to LatestSnapshot
  - Move static fields (cpu_model, cores, mem_total, motherboard, software) to Rig
  - Add dynamic fields (cpu_util, cpu_temp, mem_used, load_avg) to LatestSnapshot
  - Template reads from both Rig and LatestSnapshot
  - Same effect as Option B but splits data across two models

RECOMMENDATION: Option B
====================
This is the proven pattern already used for GPU/storage/network data.
LatestSnapshot already has 41 fields. Adding ~15 more is consistent.
Benefits:
  - Eliminates the metric_snapshot timeseries query entirely
  - All Live Metrics data comes from a single-row lookup (LatestSnapshot + Rig)
  - Template becomes simpler (one data source instead of two)
  - Static data stored once per Rig per heartbeat in LatestSnapshot
    (same as GPU/storage/network — already accepted pattern)
  - Historical data still in timeseries tables for charts

NEW FIELDS FOR LATESTSNAPSHOT:
==============================
From MetricSnapshot (currently queried for Live Metrics):
  - cpu_model (CharField 255)
  - cpu_physical_cores (PositiveIntegerField)
  - cpu_logical_cores (PositiveIntegerField)
  - cpu_utilization_pct (FloatField) — already exists
  - cpu_temp_c (FloatField) — already exists
  - cpu_load_avg_json (JSONField)
  - mem_total_bytes (BigIntegerField) — already exists
  - mem_used_bytes (BigIntegerField) — already exists
  - mem_free_bytes (BigIntegerField)
  - mem_cached_bytes (BigIntegerField)
  - swap_total_bytes (BigIntegerField)
  - swap_used_bytes (BigIntegerField)
  - motherboard_json (JSONField)
  - software_json (JSONField)
  - agent_version (CharField 20)

ALREADY IN LATESTSNAPSHOT (no change needed):
  - cpu_utilization_pct ✓
  - cpu_temp_c ✓
  - mem_used_bytes ✓
  - mem_total_bytes ✓

NET NEW FIELDS TO ADD: 12
  cpu_model, cpu_physical_cores, cpu_logical_cores, cpu_load_avg_json,
  mem_free_bytes, mem_cached_bytes, swap_total_bytes, swap_used_bytes,
  motherboard_json, software_json, agent_version

FIELDS THAT CAN BE REMOVED FROM METRICSNAPSHOT:
  None immediately — they're still needed for charts.
  But after this change, MetricSnapshot becomes purely a chart data source.
  Live Metrics will read exclusively from LatestSnapshot + Rig.

SERIALIZER CHANGES:
===================
In process_ingest(), add the new fields to LatestSnapshot update_or_create.
No changes to MetricSnapshot creation (it still stores everything for charts).

TEMPLATE CHANGES:
=================
Replace all metric_snapshot.X references with snapshot.X
(except for fields that move to Rig like motherboard_json).

RIG CHANGES:
============
Option B doesn't need Rig changes for motherboard/software — they go to LatestSnapshot.
But if we want to track hardware changes over time (like gpu_uuid), we could
also keep motherboard_json in Rig as a "current hardware" record.
This is a separate concern from the dedup optimization.
"""
print(__doc__)
