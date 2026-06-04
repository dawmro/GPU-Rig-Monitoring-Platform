# Data Flow Analysis — Issues Found

## Issue 1: Live Metrics NOT showing latest data
**Problem:** The Live Metrics page shows stale data from old test payloads, not the latest agent data.

**Root cause:** The views query `MetricSnapshot` for related data (GPU, storage, network) but the test data was inserted with different timestamps than the actual agent data. The 1-hour time window filter `timestamp__gte=timezone.now() - timedelta(hours=1)` may also exclude recent data if clocks are slightly off.

**Evidence:**
- Latest MetricSnapshot ts=2026-06-04 23:30:00 (from test payload)
- Latest GPUMetric ts=2026-06-04 23:00:00 (from actual agent, 30 min older)
- The GPU data shown on page matches the agent payload, but MetricSnapshot shows test data

**Fix needed:** Clean up test data that was inserted with fake timestamps.

## Issue 2: Test data pollution
**Problem:** Multiple test payloads were inserted during development with various timestamps, polluting the database.

**Evidence:**
- MetricSnapshot with cpu_model='Test', motherboard='Test' (fake data)
- GPUMetric records from both agent and test payloads
- NetworkMetric with 'eth0' interface (from test, not from actual agent)
- Docker containers 'ollama' and 'comfyui' (from test payload)
- AIProcessMetric records (from test payload)
- ErrorEventOccurrence records from test payloads

**Fix needed:** Delete all test data. Keep only data from actual agent heartbeats.

## Issue 3: Data not refreshing on Live Metrics page
**Problem:** The HTMX polling endpoint returns data from the database, but the latest agent data may not be the latest in the database due to:
1. Test data with newer timestamps overwriting actual agent data
2. The 1-hour time window filter potentially excluding data
3. Multiple records per device/interface — the dedup logic picks the first one in the 1-hour window, which might be test data

**Fix needed:** After cleaning test data, verify the polling returns correct latest data.

## Issue 4: `uptime_s` stored in BOTH MetricSnapshot and software_json
**Problem:** The `software_json` field already contains `uptime_s`. Now we also store it as a separate field `MetricSnapshot.uptime_s`. This is redundant but intentional — the dedicated field enables easier querying and charting.

**Verdict:** Keep both. The dedicated field is for querying efficiency, JSON is for raw data preservation.

## Issue 5: `status` field on MetricSnapshot may conflict with Rig.status
**Problem:** The serializer stores `rig.status` (which is always ONLINE at heartbeat time) into MetricSnapshot.status. But the update_rig_status command also sets status to STALE/OFFLINE. This means:
- Heartbeat sets status=ONLINE in MetricSnapshot
- update_rig_status sets status=STALE/OFFLINE in Rig model (but NOT in MetricSnapshot)

**Verdict:** This is intentional. MetricSnapshot.status records what the status was AT THE TIME of the heartbeat. The Rig.status is the current status. Both are useful for different chart types.

## Data stored where — complete mapping:

### MetricSnapshot (one row per rig per heartbeat)
- cpu_model, cpu_utilization_pct, cpu_temp_c, cpu_physical_cores, cpu_logical_cores
- cpu_load_avg_json (array of 3 floats)
- mem_total_bytes, mem_used_bytes, mem_free_bytes, mem_cached_bytes
- swap_used_bytes, swap_total_bytes
- uptime_s (NEW — tracks uptime over time)
- status (NEW — rig status at heartbeat time)
- motherboard_json (manufacturer, model, bios_version)
- software_json (hostname, os_distro, kernel, uptime_s, nvidia_driver, docker_version)
- schema_version, agent_version, timestamp

### GPUMetric (one row per GPU per heartbeat)
- gpu_index, gpu_uuid, model
- gpu_util_pct, gpu_temp_c, fan_speed_pct
- mem_total_mb, mem_used_mb, mem_free_mb, mem_util_pct
- power_draw_w, power_limit_w
- FK to MetricSnapshot

### StorageMetric (one row per device per heartbeat)
- device, mountpoint, fstype
- capacity_bytes, usage_pct, temp_c, smart_health
- FK to MetricSnapshot

### NetworkMetric (one row per interface per heartbeat)
- interface, ipv4, link_speed_mbps
- rx_bytes, tx_bytes (cumulative counters)
- rx_bytes_delta, tx_bytes_delta (NEW — bytes since last reading)
- rx_errors, tx_errors
- FK to MetricSnapshot

### DockerContainerMetric (one row per container per heartbeat)
- name, image, status, restart_count
- cpu_pct (NEW — CPU usage %)
- mem_usage_bytes, mem_limit_bytes (NEW — memory usage)
- FK to MetricSnapshot

### ErrorEvent (deduplicated by hash)
- source, message, hash, count, last_seen

### ErrorEventOccurrence (one row per error occurrence)
- FK to ErrorEvent
- timestamp

### RigStatusEvent (one row per status transition)
- status, previous_status
- timestamp

### AIProcessMetric (one row per process per heartbeat)
- process_name, pid
- gpu_uuid, gpu_mem_used_mb, cpu_pct
- FK to MetricSnapshot

### LatestSnapshot (latest values only, denormalized for fast loading)
- cpu_utilization_pct, cpu_temp_c
- mem_used_bytes, mem_total_bytes
- timestamp
