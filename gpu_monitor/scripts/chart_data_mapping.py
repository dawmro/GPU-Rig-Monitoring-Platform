#!/usr/bin/env python3
"""
Chart-to-Data-Source Mapping Analysis

Maps every chart rendered in rig_detail.html to the exact database fields it queries,
then cross-references with the duplication findings to determine:
  1. Which duplicated fields are actually NEEDED for charts
  2. Which duplicated fields could be removed without breaking any chart
  3. What the actual savings would be
"""
print("=" * 80)
print("CHART → DATA SOURCE MAPPING")
print("=" * 80)

print("""
Every chart in the Historical Charts tab calls ChartDataView with a specific metric
parameter. Here is the complete mapping from HTML canvas → JS function → API call
→ Database table → Database field.

──────────────────────────────────────────────────────────────────────────────
│ #  │ Chart Name           │ JS Function          │ API metric param   │
│────│──────────────────────│──────────────────────│────────────────────│
│  1 │ GPU Temperature      │ loadChartMultiGpu    │ gpu_temp_c         │
│  2 │ GPU Utilization      │ loadChartMultiGpu    │ gpu_util_pct       │
│  3 │ GPU VRAM Usage       │ loadChartMultiGpu    │ gpu_mem_used_mb    │
│  4 │ GPU Power Draw       │ loadChartMultiGpu    │ gpu_power_w        │
│  5 │ GPU Fan Speed        │ loadChartMultiGpu    │ gpu_fan_pct        │
│  6 │ GPU Core Clock       │ loadChartMultiGpu    │ gpu_core_clock_mhz │
│  7 │ GPU Memory Clock     │ loadChartMultiGpu    │ gpu_mem_clock_mhz  │
│  8 │ CPU Utilization      │ loadChart            │ cpu_utilization_pct│
│  9 │ CPU Temperature      │ loadChart            │ cpu_temp_c         │
│ 10 │ Memory & Swap        │ loadChartMemSwap     │ mem_used_bytes,    │
│    │                      │                      │ mem_free_bytes,    │
│    │                      │                      │ swap_used_bytes    │
│ 11 │ CPU Load Average     │ loadChartLoadAvg     │ cpu_load_avg_json  │
│ 12 │ Disk Usage           │ loadChartMultiKey    │ disk_usage_pct     │
│ 13 │ Network Traffic      │ loadChartNetwork     │ net_rx_bytes_delta,│
│    │                      │ Combined             │ net_tx_bytes_delta,│
│    │                      │                      │ net_rx_errors      │
│ 14 │ Uptime               │ loadChart            │ uptime_s           │
│ 15 │ Error Frequency      │ loadChart            │ error_frequency    │
──────────────────────────────────────────────────────────────────────────────

DATA SOURCE PER CHART (which table + field):
""")

charts = [
    ("GPU Temp",        "GPUMetric",         "gpu_temp_c",         "FloatField"),
    ("GPU Util",        "GPUMetric",         "gpu_util_pct",       "FloatField"),
    ("GPU VRAM",        "GPUMetric",         "mem_used_mb",        "IntegerField"),
    ("GPU Power",       "GPUMetric",         "power_draw_w",       "FloatField"),
    ("GPU Fan",         "GPUMetric",         "fan_speed_pct",      "FloatField"),
    ("GPU Core Clock",  "GPUMetric",         "gpu_core_clock_mhz", "IntegerField"),
    ("GPU Mem Clock",   "GPUMetric",         "gpu_mem_clock_mhz",  "IntegerField"),
    ("CPU Util",        "MetricSnapshot",    "cpu_utilization_pct","FloatField"),
    ("CPU Temp",        "MetricSnapshot",    "cpu_temp_c",         "FloatField"),
    ("Memory & Swap",   "MetricSnapshot",    "mem_used_bytes / mem_free_bytes / swap_used_bytes", "BigIntegerField"),
    ("CPU Load Avg",    "MetricSnapshot",    "cpu_load_avg_json",  "JSONField[3]"),
    ("Disk Usage",      "StorageMetric",     "usage_pct",          "FloatField"),
    ("Network Traffic", "NetworkMetric",     "rx_bytes_delta / tx_bytes_delta / rx_errors", "BigIntegerField/Int"),
    ("Uptime",          "MetricSnapshot",    "software_json.uptime_s", "JSON (inside software_json)"),
    ("Error Frequency", "MetricSnapshot",    "error_count",        "IntegerField"),
]

for name, table, field, ftype in charts:
    print(f"  {name:20s} ← {table:20s} . {field:40s} ({ftype})")

print("\n" + "=" * 80)
print("CROSS-REFERENCE: DUPLICATION vs CHART NEEDS")
print("=" * 80)

print("""
Now we cross-reference the 6 duplication candidates from the previous analysis
against the actual chart data sources.

┌────┬───────────────────────────────┬──────────────┬──────────────────────────┐
│ #  │ Duplicated Field              │ Needed for   │ Can it be removed?       │
│    │                               │ charts?      │                          │
├────┼───────────────────────────────┼──────────────┼──────────────────────────┤
│ 1  │ motherboard_json              │ NO           │ YES — not used by any   │
│    │ (in MetricSnapshot)           │              │ chart or Live Metrics    │
│    │                               │              │ display. Only used by   │
│    │                               │              │ Live Metrics card.       │
├────┼───────────────────────────────┼──────────────┼──────────────────────────┤
│ 2  │ software_json                 │ PARTIALLY    │ PARTIALLY — uptime_s is  │
│    │ (in MetricSnapshot)           │              │ needed for Uptime chart. │
│    │                               │              │ Other fields (hostname,  │
│    │                               │              │ os_distro, kernel,       │
│    │                               │              │ nvidia_driver,           │
│    │                               │              │ docker_version) are not  │
│    │                               │              │ needed per-heartbeat.    │
├────┼───────────────────────────────┼──────────────┼──────────────────────────┤
│ 3  │ gpu_uuid                      │ NO           │ YES — not used by any   │
│    │ (in GPUMetric)                │              │ chart. GPU index is     │
│    │                               │              │ used for multi-GPU      │
│    │                               │              │ chart separation.        │
├────┼───────────────────────────────┼──────────────┼──────────────────────────┤
│ 4  │ rig_uuid + timestamp          │ YES          │ NO — needed for chart   │
│    │ (in child tables)             │              │ queries (filtered by    │
│    │                               │              │ rig_uuid + timestamp)   │
│    │                               │              │ and UNIQUE constraints. │
├────┼───────────────────────────────┼──────────────┼──────────────────────────┤
│ 5  │ cpu_load_avg_json             │ YES          │ NO — needed for CPU     │
│    │ (in MetricSnapshot)           │              │ Load Average chart.     │
│    │                               │              │ Could be 3 FloatFields  │
│    │                               │              │ instead of JSON, but    │
│    │                               │              │ same data volume.       │
├────┼───────────────────────────────┼──────────────┼──────────────────────────┤
│ 6  │ LatestSnapshot denormalization│ NO (charts   │ BY DESIGN — intentional │
│    │                               │ use timeseries│ for display performance.│
│    │                               │ tables)      │ Not removable.          │
└────┴───────────────────────────────┴──────────────┴──────────────────────────┘
""")

print("=" * 80)
print("DETAILED ANALYSIS PER CANDIDATE")
print("=" * 80)

print("""
═══ CANDIDATE 1: motherboard_json — SAFE TO REMOVE from MetricSnapshot ═══

  Charts that use motherboard data: NONE
  Live Metrics that use motherboard: YES (Live Metrics card shows mobo info)
  
  Current storage: ~100-300 bytes per MetricSnapshot row
  For 1,000 rigs × 1,440 rows/day = ~144-432 MB/day
  
  Where it SHOULD live:
    - Rig model (static, one-time data — set on rig creation)
    - Or a separate RigProfile table (if mobo can change)
  
  Impact on charts: ZERO — no chart queries motherboard_json
  Impact on Live Metrics: Must update _fetch_rig_metrics() to read from
    Rig.motherboard_json instead of LatestSnapshot (which currently gets
    it from MetricSnapshot during ingest).
  
  Verdict: HIGH SAVINGS, LOW RISK. Move to Rig model.

═══ CANDIDATE 2: software_json — PARTIALLY REMOVABLE ═══

  Charts that use software_json fields:
    - Uptime chart → software_json.uptime_s (READ from MetricSnapshot)
  
  Other software_json fields (hostname, os_distro, kernel, nvidia_driver,
  docker_version) are NOT used by any chart.
  
  Current storage: ~200-500 bytes per MetricSnapshot row
  For 1,000 rigs × 1,440 rows/day = ~288-720 MB/day
  
  What can be removed:
    - hostname, os_distro, kernel → move to Rig model (static)
    - nvidia_driver → move to Rig model (semi-static, changes on driver update)
    - docker_version → move to Rig model (semi-static)
  
  What must stay (for Uptime chart):
    - uptime_s → MUST remain in MetricSnapshot (or move to dedicated field)
  
  Optimization: Keep only uptime_s in MetricSnapshot, move rest to Rig.
  Savings: ~200-400 bytes/row → ~288-576 MB/day for 1,000 rigs
  
  Verdict: MEDIUM-HIGH SAVINGS, MEDIUM RISK. Need to update Uptime chart
  query and Live Metrics to read from new location.

═══ CANDIDATE 3: gpu_uuid — SAFE TO REMOVE from GPUMetric ═══

  Charts that use gpu_uuid: NONE
  Live Metrics: Uses gpu_index (not uuid) for GPU identification
  
  Current storage: ~36 bytes per GPUMetric row
  For 1,000 rigs × 5.3 GPUs × 1,440 rows/day = ~275 MB/day
  
  Where it SHOULD live:
    - A GPU inventory table: (rig_uuid, gpu_index, gpu_uuid, model, mem_total_mb)
    - Or Rig model as JSON: gpu_inventory_json
  
  Impact on charts: ZERO
  Impact on Live Metrics: None (uses gpu_index)
  
  Verdict: HIGH SAVINGS, LOW RISK. Move to GPU inventory.

═══ CANDIDATE 4: rig_uuid + timestamp in child tables — NOT REMOVABLE ═══

  Charts DO query child tables directly:
    - GPU charts: GPUMetric.objects.filter(rig_uuid=..., timestamp__gte=...)
    - Disk chart: StorageMetric.objects.filter(rig_uuid=..., timestamp__gte=...)
    - Network chart: NetworkMetric.objects.filter(rig_uuid=..., timestamp__gte=...)
  
  The (rig_uuid, timestamp) in child tables is REQUIRED for:
    1. Chart queries (filter by rig_uuid + timestamp range)
    2. UNIQUE constraints: (rig_uuid, timestamp, gpu_index/device/interface)
    3. NetworkMetric delta calculation (SELECT previous by rig_uuid + interface)
  
  Theoretical optimization: Replace rig_uuid with snapshot_id FK.
  - Saves 8 bytes/row (snapshot_id=8B vs rig_uuid=16B)
  - But ALL chart queries would need JOINs to MetricSnapshot for timestamp
  - Net effect: worse query performance for marginal savings
  
  Verdict: NOT WORTH IT. The 24 bytes/row is the cost of the current query
  pattern. Removing it would break chart queries or require expensive JOINs.

═══ CANDIDATE 5: cpu_load_avg_json — KEEP (but optimize format) ═══

  Charts that use it: CPU Load Average chart (reads cpu_load_avg_json[0,1,2])
  
  Current storage: ~30 bytes per MetricSnapshot row (JSON array of 3 floats)
  
  Optimization: Replace JSONField[3] with 3 FloatFields:
    - cpu_load_avg_1m (FloatField)
    - cpu_load_avg_5m (FloatField)
    - cpu_load_avg_15m (FloatField)
  
  Storage: 3 × 8 bytes = 24 bytes (vs ~30 bytes JSON) — marginal savings
  Benefit: Simpler queries, no JSON parsing, indexable individually
  
  Verdict: LOW SAVINGS, LOW RISK. Nice-to-have for code clarity, not
  a storage priority.

═══ CANDIDATE 6: LatestSnapshot — BY DESIGN, NOT REMOVABLE ═══

  Charts do NOT use LatestSnapshot — they query timeseries tables directly.
  LatestSnapshot is used ONLY by:
    - Fleet Overview (rig_list): 0 timeseries queries
    - Live Metrics (htmx_metrics): single-row lookup for display
  
  This is intentional denormalization documented in Architecture §5.5:
  "Display data (latest values) is stored in LatestSnapshot during ingest.
   Chart data (historical trends) is stored in timeseries tables."
  
  Removing LatestSnapshot would:
    - Break Fleet Overview (back to 2002+ queries)
    - Break Live Metrics (back to ~1500ms with DISTINCT ON)
    - No impact on charts
  
  Verdict: DO NOT REMOVE. This is the core performance optimization.
""")

print("=" * 80)
print("FINAL RANKING: WHAT TO ACTUALLY DO")
print("=" * 80)

print("""
Priority │ Action                         │ Savings (1K rigs) │ Risk
─────────┼────────────────────────────────┼──────────────────┼──────
   1     │ Move motherboard_json to Rig   │ ~144-432 MB/day  │ Low
         │ model                          │                  │
         │                                │                  │
   2     │ Move gpu_uuid to GPU inventory │ ~275 MB/day      │ Low
         │ table                          │                  │
         │                                │                  │
   3     │ Split software_json: keep     │ ~200-400 MB/day  │ Medium
         │ uptime_s in MetricSnapshot,    │                  │
         │ move rest to Rig model         │                  │
         │                                │                  │
   4     │ Replace cpu_load_avg_json     │ ~5-10 MB/day     │ Low
         │ with 3 FloatFields             │ (marginal)       │
         │                                │                  │
   5     │ Remove rig_uuid+timestamp from │ ~173 MB/day      │ HIGH
         │ child tables (snapshot_id FK)  │ (not worth it)   │
─────────┴────────────────────────────────┴──────────────────┴──────

TOTAL ESTIMATED SAVINGS (items 1-3): ~619-1107 MB/day for 1,0,000 rigs
  = ~18.5-33.2 GB over 31 days
  = ~12-21% of the ~157 GB total (31 days, 1,000 rigs)

Items 4 and 5 are NOT recommended:
  - Item 4: marginal savings, high query pattern change
  - Item 5: negligible savings, code clarity only
""")
