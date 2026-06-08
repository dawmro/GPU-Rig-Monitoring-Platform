# Feature Ideas — Monitoring Rigs Working Parameters

## Current State Analysis

### What's Collected
- CPU: model, cores, load avg, utilization, temp
- Memory: total, used, free, cached, swap
- GPU: model, uuid, util, temp, fan, memory, power draw/limit
- GPU processes: pid, type, name, memory per GPU
- Storage: device, mountpoint, capacity, usage%, temp, SMART
- Network: interface, rx/tx bytes, rx/tx deltas, errors, link speed, ipv4
- Docker: name, image, status, restarts, cpu%, memory
- Motherboard: manufacturer, model, bios version
- Software: hostname, os distro, kernel, uptime, nvidia driver, docker version
- Errors: source, message, timestamp from journalctl

### What's Displayed (Live Metrics)
- CPU: model, cores, util bar, temp, load avg
- Memory: used/total, free, cached, swap bar
- Storage: per-device usage bar, temp
- GPU: per-GPU model, util bar, temp, fan, memory bar, power
- GPU Processes: per-process name, type badge, memory
- Network: per-interface rx/tx/errors with dual Y-axes
- Motherboard: manufacturer, model, bios
- Software: hostname, os, kernel, uptime, nvidia driver, docker version
- Docker: per-container cpu%, memory, restarts
- Errors: source, message, timestamp, count

### What's Collected But NOT Displayed
- `agent_version` — stored in MetricSnapshot but never shown
- `schema_version` — stored but never shown
- `gpu_uuid` — stored in GPUMetric but not shown in live metrics
- `link_speed_mbps` — stored in NetworkMetric but not shown
- `ipv4` — stored in NetworkMetric, partially shown in chart labels only
- `power_limit_w` — stored but not shown in live metrics (only power_draw_w is)
- `mem_util_pct` — stored in GPUMetric but not shown
- `mem_free_mb` — stored in GPUMetric but not shown

---

## Top 3 Feature Ideas

### 1. ⚡ Power Efficiency Monitoring (GPU Watts per MH/s or per Work Unit)

**Concept:** Track power efficiency over time — how much work is done per watt consumed. For mining rigs, this is watts per hash rate. For AI training, watts per token/sample.

**Implementation:**
- New model `PowerEfficiencyMetric` with fields: `rig_uuid`, `timestamp`, `gpu_index`, `power_draw_w`, `work_units`, `efficiency_w_per_unit`
- Agent collects work units from miner API or nvidia-smi (if available)
- Server stores and charts efficiency over time
- Dashboard shows: current efficiency, 24h/7d/30d trend, alerts when efficiency drops below threshold

**Pros:**
- Directly impacts profitability for mining rigs
- Detects GPU degradation, thermal throttling, or misconfiguration early
- Easy to understand metric (lower watts per unit = better)
- Charts fit naturally into existing Historical Charts framework

**Cons:**
- Work units vary by workload (mining hash rate vs AI tokens) — needs configuration
- Not all workloads report work units via standard APIs
- Adds complexity to agent (may need to query miner-specific APIs)
- Requires user to configure what "work unit" means for their setup

**Difficulty:** Medium (3/5)
- Agent: Need to add configurable work unit collector (could start with nvidia-smi GPU util% as proxy)
- Server: New model + migration, new chart endpoint
- Dashboard: New chart card, new Live Metrics display
- ~200 lines of new code total

**Usefulness:** High (5/5) — directly tied to rig profitability and health

---

### 2. 🌡️ Thermal Throttling Detection & Alerting

**Concept:** Detect when GPUs are thermal throttling (reducing performance due to heat). Show throttling events and duration in dashboard.

**Implementation:**
- Agent already collects `gpu_temp_c` and `gpu_util_pct`
- Add thermal throttle detection: if temp > threshold (e.g., 83°C) AND util < expected, flag as throttling
- New model `ThermalEvent` with fields: `rig_uuid`, `timestamp`, `gpu_index`, `temp_c`, `util_pct`, `throttle_duration_s`
- Dashboard shows: current throttle status (Live Metrics), throttle history chart (Historical Charts), total throttle time per day
- Optional: webhook/notification when throttle exceeds threshold

**Pros:**
- Uses existing data — no new collectors needed
- Critical for mining/AI rigs where thermal throttling = lost revenue
- Easy to detect: high temp + low util = throttling
- Fits naturally into existing GPU card UI
- Can alert before permanent GPU damage

**Cons:**
- "Expected util" is hard to define — workload varies
- May produce false positives during legitimate low-util periods
- Requires per-GPU-type temperature thresholds (RTX 3060 vs A100 have different limits)
- Adds new model + migration

**Difficulty:** Low-Medium (2/5)
- Agent: No changes needed (uses existing temp + util data)
- Server: New `ThermalEvent` model, detection logic in `process_ingest()`
- Dashboard: New indicator in GPU card, new Historical Chart
- ~100 lines of new code

**Usefulness:** High (5/5) — prevents hardware damage, optimizes performance

---

### 3. 📊 Rig Health Score & Trend Dashboard

**Concept:** Composite health score (0-100) based on multiple factors: temperature, power efficiency, error rate, uptime, thermal throttling. Show score trend over time.

**Implementation:**
- New model `RigHealthScore` with fields: `rig_uuid`, `timestamp`, `score`, `factors_json`
- Score calculation (weighted):
  - Temperature: 30% (penalty for high temps)
  - Error rate: 25% (penalty for frequent errors)
  - Uptime: 20% (penalty for downtime)
  - Power efficiency: 15% (penalty for low efficiency)
  - Thermal throttling: 10% (penalty for throttle events)
- Dashboard shows: current score (big number), score trend chart (7d/30d), factor breakdown
- Fleet overview: sort by health score, color-code (green/yellow/red)

**Pros:**
- Single number to quickly assess rig health
- Combines all existing data — no new collectors needed
- Trend chart shows degradation over time (predictive maintenance)
- Fleet overview sorting helps prioritize which rigs need attention
- Easy to understand: 90+ = good, 70-90 = watch, <70 = investigate

**Cons:**
- Score weights are subjective — may need per-user customization
- Doesn't tell you *what* is wrong, just *that* something is wrong
- Requires historical data to be meaningful (first day will be noisy)
- Adds new model + migration

**Difficulty:** Low (2/5)
- Agent: No changes needed
- Server: New `RigHealthScore` model, score calculation in `update_rig_status` command
- Dashboard: New card in Live Metrics, new Historical Chart, fleet overview column
- ~150 lines of new code

**Usefulness:** High (4/5) — quick health assessment, but doesn't replace detailed metrics

---

## Comparison Matrix

| Feature | Usefulness | Difficulty | New Collectors | New Models | Dashboard Changes |
|---|---|---|---|---|---|
| Power Efficiency | ⭐⭐⭐⭐⭐ (5) | ⭐⭐⭐ (3) | Yes (work units) | Yes | Medium |
| Thermal Throttling | ⭐⭐⭐⭐⭐ (5) | ⭐⭐ (2) | No | Yes | Small |
| Health Score | ⭐⭐⭐⭐ (4) | ⭐⭐ (2) | No | Yes | Medium |

## Recommendation

**Start with #2 (Thermal Throttling)** — highest value, lowest effort:
- Uses existing data, no new collectors
- New model is simple (5 fields)
- Dashboard changes are minimal (add indicator to existing GPU card)
- Immediate practical value for any GPU rig

**Then #3 (Health Score)** — builds on thermal data:
- Also uses existing data
- Composite score is easy to calculate
- Fleet overview sorting is highly useful for multi-rig operators

**Then #1 (Power Efficiency)** — highest effort but highest value:
- Requires new collector (work units)
- Most complex to implement and configure
- But directly tied to profitability

## Additional Quick Wins (No New Models)

These could be implemented immediately without any database changes:

1. **Show `agent_version` in Software card** — already stored, just display it
2. **Show `power_limit_w` in GPU card** — already stored, just display it
3. **Show `link_speed_mbps` in Network card** — already stored, just display it
4. **Show `gpu_uuid` (truncated) in GPU card** — already stored, useful for identifying specific GPUs
5. **Color-code GPU temp in Live Metrics** — already done in charts, but not in live cards
6. **Show rig uptime in Fleet Overview** — already have `last_seen`, just format it
