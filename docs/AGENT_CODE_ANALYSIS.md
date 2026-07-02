# Agent Code Analysis - Linux vs Windows

## Overview

Both agents share similar structure but have platform-specific collectors:
- **Linux agent** (`agent/run.py`): Uses `/sys` filesystem for GPU, CPU power via RAPL
- **Windows agent** (`agent_windows/run.py`): Uses WMI for GPU, no RAPL support

**Schedule**: Both agents run every 60 seconds via cron/Task Scheduler.
**Note**: User mentioned "hour" but code/architecture indicates 60s interval.

## Error Handling Analysis

### Silent Failures Found

| Location | Issue | Impact |
|----------|-------|--------|
| agent/run.py:72-73 | `except Exception: pass` in CPU temp collection | Silent failure - temp_c stays None, user sees no temp |
| agent/run.py:173-174 | `except Exception: pass` in CPU model detection | Silent failure - model shows "Unknown" |
| agent/run.py:196-197 | `except Exception: pass` in cpuinfo import | Silent fallback to "Unknown" model |
| agent/run.py:395-400 | `except Exception: pass` in docker ps parsing | Docker containers silently skipped |
| agent/run.py:530-531 | `except Exception: pass` in storage SMART parsing | SMART health data silently lost |
| agent/run.py:553-556 | `except ValueError: pass` then `except Exception: pass` | Individual disk errors silently swallowed |
| agent/run.py:937-938 | `except Exception: pass` in collect_software uptime | `uptime_s` missing from payload |
| agent_windows/run.py:95-96 | `except Exception: pass` in UUID save | UUID not persisted if file write fails |

### Issues Found

**1. Generic Exception Handling (Lines 72, 173, 196, 400, etc.)**
```python
except Exception:
    pass
```
Problem: Entire exception category swallowed silently. Better: Catch specific exceptions, log the error.

**2. Missing Return Values**
Many collectors return `None` or `{}` on failure without indicating why:
- `collect_cpu()` returns `{}` on error (line 210)
- `collect_top_processes()` returns `None` on error (line 924)
- `collect_errors()` returns `[]` on error (line 977)

Problem: Server receives incomplete payload, no indication of failure.

**3. Empty List Returns Without Explanation**
`collect_docker()` returns `[]` on failure without logging (line 563). Ambiguous - user can't distinguish "no containers" vs "collection failed".

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
        'cpu': dict,           # Required: model, physical_cores, logical_cores, load_avg, utilization_pct, temp_c, freq
        'memory': dict,        # Required: total_bytes, used_bytes, free_bytes, cached_bytes, swap_*
        'storage': list,       # Each: device, mountpoint, usage_pct, temp_c, etc.
        'network': list,       # Each: interface, rx_bytes, tx_bytes, etc.
        'gpus': list,          # Each: model, temp_c, gpu_util_pct, etc.
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
- Uses `.get()` with fallback defaults throughout (lines 100-116)
- Handles `None` gracefully: `top_processes.get('by_cpu', []) if top_processes else []` (lines 484-486)
- Missing fields → `None`/`[]` stored in database (no crash)
- **Key insight**: Serializer is resilient to missing/None values

### Safe Changes (No Breaking Impact)

| Change | Safe? | Notes |
|--------|-------|-------|
| Add new optional field | ✅ | `.get()` returns None |
| Return `{}` on failure | ✅ | Serializer uses `.get()` defaults |
| Return `None` instead of dict | ✅ | Serializer checks `if var else []` |
| Remove field from payload | ⚠️ | Ensure serializer `.get()` handles missing |
| Change field value type | ❌ | Serializer may crash/truncate |

## Code Structure Opportunities

**1. Collector Pattern**
Current pattern per collector returns `None` or `{}` on failure. Safe because serializer uses `.get()`.

**2. Shared Code Extraction**
Both agents have ~70% duplicated code. Opportunities:
- Shared base module: Common collectors (memory, network, processes, software)
- Platform-specific hooks: GPU, storage, power as separate modules

**3. Async Collection**
Current: Sequential collection with blocking `time.sleep(0.5)` for process CPU%.
Could use: Single-pass collection with proper baseline calls.

**4. Config Validation**
Current uses inline validation with `sys.exit(2)`. Could use: Pydantic validation.

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

1. **Add structured error reporting**: Include `collection_errors: {...}` in payload for debugging
2. **Log specific exceptions**: Replace bare `except Exception: pass` with logged warnings
3. **Distinguish empty vs error**: Return `{'error': 'message'}` for debugging visibility
4. **Extract common code**: Create shared module for ~70% duplicated logic
5. **Add health metrics**: Agent uptime, collection success rate
6. **Current changes are safe**: Existing error handling patterns won't break serializer