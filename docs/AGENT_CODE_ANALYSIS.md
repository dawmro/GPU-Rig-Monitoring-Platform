# Agent Code Analysis - Linux vs Windows

## Overview

Both agents share similar structure but have platform-specific collectors:
- **Linux agent** (`agent/run.py`): Uses `/sys` filesystem for GPU, CPU power via RAPL
- **Windows agent** (`agent_windows/run.py`): Uses WMI for GPU, no RAPL support

**Schedule**: Both agents run every 60 seconds via cron/Task Scheduler.

## Error Handling Analysis

### Silent Failures - Actually Intentional Graceful Degradation

The bare `except Exception: pass` patterns are **intentional graceful degradation**, NOT bugs:

| Location | Why Silent Failure Makes Sense |
|----------|-------------------------------|
| agent/run.py:72-73 | UUID persistence - optional. Agent continues with in-memory UUID. |
| agent/run.py:173-174 | CPU temp - optional hardware data. None is valid value. |
| agent/run.py:196-197 | cpuinfo module - optional dependency. "Unknown" is valid fallback. |
| agent/run.py:395-400 | Docker collection - may not be installed. Empty list is valid. |
| agent/run.py:530-531 | SMART data - optional hardware info. None valid. |
| agent/run.py:553-556 | Per-disk errors - non-critical. Continue collecting other disks. |
| agent/run.py:937-938 | Uptime - optional metadata. None valid. |
| agent_windows/run.py:95-96 | UUID save - same as Linux: continue with in-memory UUID. |

### Why Specific Exception Handling Would NOT Help

**Current behavior is correct:**
- Non-critical metrics that fail should return `None`/`[]` (serializer handles this)
- Critical validation (missing API key, endpoint) already uses `sys.exit(2)` with clear error
- Adding `logging.warning()` for minor hardware failures creates log noise without changing outcome

**What specific exceptions would we catch?**
- `PermissionError`, `FileNotFoundError` - appropriate for file access
- `ImportError`, `ModuleNotFoundError` - appropriate for optional modules
- But logging these doesn't change behavior - data is still missing

### What Agent DOES Log Specifically

Line 186-189 shows good pattern already exists:
```python
except (AttributeError, OSError, NotImplementedError) as e:
    logging.getLogger('cpu').debug('CPU frequency unavailable: %s', e)
except Exception as e:
    logging.getLogger('cpu').warning('CPU frequency collection failed: %s', e)
```

This proves the codebase already has the right patterns - silent failures are for truly optional data.

## Payload Structure & Server Handling

### Current Payload (agent/run.py:982-1019)
```python
{
    'rig_uuid': str,
    'rig_name': str,
    'schema_version': str,
    'agent_version': str,
    'timestamp': iso8601,
    'metrics': {
        'cpu': dict,
        'memory': dict,
        'storage': list,
        'network': list,
        'gpus': list,
        'gpu_processes': list,
        'docker_containers': list,
        'top_processes': dict,
    },
    'motherboard': dict,
    'software': dict,
    'errors': list,
    'power': dict,
}
```

### Server Serializer Handling (serializers.py)
- Uses `.get()` with fallback defaults throughout
- Handles `None` gracefully with `if var else []` pattern
- Missing fields → `None`/`[]` stored in database (no crash)

**Key insight**: Serializer is resilient to missing/None values - safe for agent changes that remove/add optional fields.

### Safe Changes (No Breaking Impact)

| Change | Safe? | Notes |
|--------|-------|-------|
| Add new optional field | ✅ | `.get()` returns None |
| Return `{}` on failure | ✅ | Serializer uses `.get()` defaults |
| Return `None` instead of dict | ✅ | `if var else []` handles it |
| Remove field from payload | ⚠️ | Only if serializer doesn't use it |
| Change field value type | ❌ | Serializer may crash/truncate |

## CPU% Measurement Analysis

**Current approach (lines 132, 899, 913):**
```python
# Line 132 - Overall CPU
cpu_percent = psutil.cpu_percent(interval=1)  # BLOCKS for 1 second

# Lines 899, 913 - Per-process CPU (two-pass)
proc_obj.cpu_percent(interval=None)  # baseline (non-blocking)
time.sleep(0.5)                      # BLOCKS for 0.5 seconds
proc_obj.cpu_percent(interval=None)  # actual measurement
```

**Timing breakdown:**
- Overall CPU: **1 second blocking** - gives accurate system-wide utilization
- Per-process CPU: **0.5 seconds blocking sleep** + process object creation time
- Total CPU-related time: **~1.5 seconds minimum**

### Problems with Async/Threading Improvement

| Edge Case | Problem |
|-----------|---------|
| Process exits during measurement | PID might not exist for second pass |
| Process list changes | New/missed processes between passes |
| Short-lived processes | Two-pass misses processes alive only briefly |
| Zombie processes | cpu_percent() may behave unexpectedly |
| Permission changes | Process might become inaccessible |
| psutil version differences | Behavior varies across versions |
| Threading in embedded Python | Some environments restrict threading |
| Windows WSL compatibility | WSL2 has different process model |

### Why Current Approach IS Optimal

1. **Accurate**: Two-pass gives real CPU% (not instant snapshots)
2. **Bounded**: 0.5s sleep is predictable, won't exceed 30s timeout
3. **Simple**: No threading/process pool complexity
4. **Reliable**: Works on both Linux/Windows with same code
5. **No race conditions**: Process objects held in memory between passes

### Alternative Considered - Single-Pass Per Process
```python
# Would be faster but less accurate and actually worse:
for p in psutil.process_iter():
    p.info['cpu_pct'] = p.cpu_percent(interval=0.1)  # 100ms per process
```
This would take **2 seconds per process** for 20 processes = 40 seconds! Worse than blocking 0.5s once.

**Recommendation**: Keep two-pass blocking approach. It's the right trade-off between accuracy and performance.

## Shared Module Challenges

A shared module would significantly complicate deployments:

### Installation Differences

| Aspect | Linux | Windows | Shared Module Problem |
|--------|-------|---------|----------------------|
| Install script | `agent/install.sh` (bash) | Manual copy | Cross-platform installer needed |
| Install location | `/opt/monitoring-agent/` | User-chosen dir | Different paths, imports break |
| Dependencies | psutil, py-cpuinfo, nvidia-ml-py3 | psutil, py-cpuinfo, wmi, pynvml | Different optional deps |
| Scheduling | `/etc/cron.d/` | `schtasks` CLI | Separate logic |
| Permissions | sudoers + system user | Administrator | Different privilege model |

### Import Complexity
If `agent_common/` created:
- Linux agent would need `sys.path.append('../agent_common')`
- Windows agent same, but install path varies
- Both install scripts must copy the shared module
- Version drift risk between platforms

### Recommendation: Keep Separate
1. Simple installs per platform (no path resolution)
2. Isolated troubleshooting (platform-specific files)
3. No shared dependency management
4. Use `sync_agents.py` script for intentional code sync when needed

## Key Differences (Linux vs Windows)

| Feature | Linux | Windows |
|---------|-------|---------|
| GPU Collection | nvidia-ml-py + /sys for AMD | NVIDIA Control Object via WMI |
| CPU Power | RAPL via /sys/class/powercap | Estimated (no RAPL) |
| Storage SMART | /sys or smartctl | wmi.Win32_DiskDrive |
| Docker | CLI with sudo fallback | CLI direct |
| Locking | signal.alarm timeout | AcquisitionLock (file-based) |
| Scheduling | cron | Windows Task Scheduler |

## Recommendations

**Current error handling is correct**. Do NOT change `except Exception: pass` patterns for optional metrics.

### What WOULD be improvements:
- `collection_errors` dict (optional) - Debugging visibility without breaking serializer
- Better docstrings explaining optional nature of each collector
- Sync script to keep agents consistent

### What WOULD break things:
- Changing return types (serializer field types are fixed)
- Removing required fields (`cpu`, `memory` dicts)
- Adding required fields without serializer update