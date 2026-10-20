# Plan: Increase Process cmdline Character Limit from 200 to 400

## Current State

### Agent Collection (`agent/run.py`, `agent_windows/run.py`)
- `collect_top_processes(limit=20)` - collects top 20 by CPU% and top 20 by memory%
- **`cmdline` truncated to 200 characters** per process (line 1516 in Linux, line 1646 in Windows)

### Dashboard View (`gpu_monitor/dashboard/views.py`)
- `_build_process_details()` takes `[:10]` from each list (top_cpu_processes_json and top_mem_processes_json)
- **Number of processes: stays at 10 each (20 max after dedup)** - NO CHANGE

### Template (`gpu_monitor/templates/dashboard/_metrics_cards.html`)
- Process Details card displays full `{{ proc.cmdline }}` (line 784)
- Top CPU/Memory tables show truncated name with title tooltip (lines 722, 749)

### Database Model (`gpu_monitor/metrics_app/models.py`)
- `top_cpu_processes_json` - stores top 20 by CPU%
- `top_mem_processes_json` - stores top 20 by memory%
- JSONField has no character limit

---

## Required Changes

### 1. Linux Agent (`agent/run.py`)
**Line:** 1516

Change:
```python
info['cmdline'] = ' '.join(cmdline_parts)[:200]
```
To:
```python
info['cmdline'] = ' '.join(cmdline_parts)[:400]
```

### 2. Windows Agent (`agent_windows/run.py`)
**Line:** 1646

Change:
```python
info['cmdline'] = ' '.join(cmdline_parts)[:200]
```
To:
```python
info['cmdline'] = ' '.join(cmdline_parts)[:400]
```

### 3. Documentation (`docs/GPU_Rig_Monitoring_Architecture.md`)
**Line:** 2087

Update:
> **Data source:** `top_cpu_processes_json` ∪ `top_mem_processes_json` from `LatestSnapshot`. Agent truncates `cmdline` to **400 chars** to bound payload size.

---

## Impact Analysis

| Component | Current Limit | New Limit | Change Required |
|-----------|--------------|-----------|-----------------|
| Linux agent cmdline | 200 chars | 400 chars | **YES** |
| Windows agent cmdline | 200 chars | 400 chars | **YES** |
| Number of processes | 10 each | 10 each | **NO** |
| Database storage | JSONField | JSONField | None |
| Template display | Full cmdline | Full cmdline | Auto (displays whatever stored) |
| Payload size | ~200/proc | ~400/proc | ~+2 KB per heartbeat |

### Payload Size Calculation
- 20 processes × 200 extra chars = 4,000 extra chars per heartbeat
- ~4 KB additional per heartbeat (negligible on modern networks)
- Heartbeat interval: ~60 seconds

---

## Implementation Order

1. `agent/run.py` - line 1516: `[:200]` → `[:400]`
2. `agent_windows/run.py` - line 1646: `[:200]` → `[:400]`
3. `docs/GPU_Rig_Monitoring_Architecture.md` - line 2087: update documentation

---

## Verification

After implementation:
1. Deploy updated agent to test rig
2. Check Process Details card - commands should no longer be cut off
3. Verify payload size in network tab (expect ~4 KB increase)
4. Check database - `cmdline` field should contain up to 400 chars