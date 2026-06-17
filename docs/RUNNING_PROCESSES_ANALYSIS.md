# Running Processes — Live Metrics Feature Analysis

## Current State

The Live Metrics card (`_metrics_cards.html`) already displays **GPU Processes** (from `nvidia-smi`) showing processes that use GPU. But there is NO display of general system processes (like `top -n 1` output).

### What exists today:
- **GPU Processes**: nvidia-smi parsed output → GPU index, PID, type (C/G/C+G), name, GPU memory
- Shown in a compact list below the GPU cards

### What's missing:
- **System processes**: CPU/memory usage per process, like `top` or `ps aux`
- No visibility into what's consuming CPU/RAM beyond the aggregate percentages

---

## Research Findings

### psutil capabilities (Linux agent)

`psutil.process_iter()` provides per-process:
- `pid`, `name`, `username`
- `cpu_percent` — CPU usage percentage (0-100% per core)
- `memory_percent` — RAM usage percentage
- `memory_info` — RSS, VMS bytes
- `status` — running, sleeping, zombie, etc.
- `create_time` — process start timestamp
- `num_threads` — thread count
- `cmdline` — full command line
- `ppid` — parent PID
- `nice` — process priority
- `io_counters` — read/write bytes (Linux only)
- `num_fds` — open file descriptors

### Windows compatibility

On Windows, `psutil` provides the same fields but some differ:
- `cpu_percent` works the same
- `memory_percent` works the same
- `io_counters` not available
- `num_fds` not available

### Collection approach

Two possible strategies:

**Strategy A: Top-N processes (recommended)**
- Collect top N processes by CPU or memory on the agent side
- Send as a compact JSON array in the payload
- Minimal payload size, minimal server processing
- Example: top 20 processes by CPU + top 20 by memory = ~40 entries
- Each entry: `{pid, name, cpu_pct, mem_pct, username, cmdline}`
- Payload overhead: ~5-10 KB (acceptable)

**Strategy B: Full process list**
- Send all processes to the server
- Server handles sorting/filtering for display
- Payload overhead: ~50-100 KB for 300+ processes
- More flexible but wasteful — most processes are idle

**Recommended: Strategy A** — collect top-N on agent, as done for GPU processes

### Collection implementation on agent

```python
def collect_top_processes(limit=20):
    """Collect top processes by CPU and memory."""
    try:
        import psutil
        attrs = ['pid', 'name', 'cpu_percent', 'memory_percent',
                 'username', 'status', 'num_threads', 'cmdline']
        procs = []
        for p in psutil.process_iter(attrs):
            info = p.info
            info['cmdline'] = ' '.join(info.get('cmdline') or [])[:200]
            procs.append(info)
        
        # Top by CPU
        by_cpu = sorted(procs, key=lambda x: x.get('cpu_percent', 0), reverse=True)[:limit]
        # Top by memory
        by_mem = sorted(procs, key=lambda x: x.get('memory_percent', 0), reverse=True)[:limit]
        
        return {
            'by_cpu': by_cpu,
            'by_mem': by_mem,
            'total_count': len(procs),
        }
    except Exception as e:
        logging.getLogger('processes').warning('Process collection failed: %s', e)
        return None
```

### Server-side storage

**Option A: LatestSnapshot only (recommended for Live Metrics)**
- Store in LatestSnapshot as JSON arrays: `top_cpu_processes_json`, `top_mem_processes_json`
- No timeseries storage needed — only latest values for display
- Minimal DB impact

**Option B: Timeseries table**
- Create a new `ProcessMetric` model for historical tracking
- Higher storage cost, more complex
- Only needed if we want historical process tracking (unlikely)

**Recommended: Option A** — LatestSnapshot JSON arrays, same pattern as GPU/storage/network data

### Display design

Following the existing Live Metrics card pattern (GPU Processes section):

```
┌─────────────────────────────────────────────────┐
│ Top Processes (by CPU)              12 total     │
├─────────────────────────────────────────────────┤
│ PID      Process Name    CPU%   Mem%  User       │
│ 3502     firefox         34.1%  8.5%  qrv        │
│ 1688     python          12.3%  6.8%  qrv        │
│ 2020     gnome-shell     10.6%  1.8%  qrv        │
│ ...                                              │
├─────────────────────────────────────────────────┤
│ Top Processes (by Memory)           12 total     │
├─────────────────────────────────────────────────┤
│ PID      Process Name    CPU%   Mem%  User       │
│ 3502     firefox         34.1%  8.5%  qrv        │
│ ...                                              │
└─────────────────────────────────────────────────┘
```

### Schema changes needed

**Agent payload (new section):**
```json
"top_processes": {
    "by_cpu": [
        {"pid": 3502, "name": "firefox", "cpu_percent": 34.1, "memory_percent": 8.5,
         "username": "qrv", "num_threads": 93, "cmdline": "/usr/lib/firefox/firefox"}
    ],
    "by_mem": [...],
    "total_count": 364
}
```

**Server model (LatestSnapshot):**
```python
top_cpu_processes_json = models.JSONField(default=list, blank=True)
top_mem_processes_json = models.JSONField(default=list, blank=True)
process_count = models.PositiveIntegerField(default=0)
```

---

## Implementation Plan

### Phase 1: Agent (Linux + Windows)
1. Add `collect_top_processes()` to `agent/run.py` and `agent_windows/run.py`
2. Add `top_processes` to the payload
3. Agent version bump: 1.5.9 → 1.5.10, schema 1.7 → 1.8

### Phase 2: Server
1. Add fields to `LatestSnapshot` model + migration
2. Update `process_ingest()` to store process data
3. Update `_fetch_rig_metrics()` to include process data
4. Update `_metrics_cards.html` to display process tables

### Phase 3: Documentation
1. Update `DATA_FLOW_ANALYSIS.md` with process fields
2. Update `GPU_Rig_Monitoring_Architecture.md` schema
3. Update agent version references
