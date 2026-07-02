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
- Uses `.get()` with fallback defaults (lines 100-116)
- Handles `None` gracefully with `if var else []` pattern (lines 484-486)
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

1. **Add structured error reporting**: Include `collection_errors` in payload
2. **Log specific exceptions**: Replace bare `except Exception: pass` with logged warnings
3. **Distinguish empty vs error**: Return `{'error': 'message'}` for debugging
4. **Keep agents separate**: Avoid shared module complexity
5. **Current changes are safe**: Existing error handling won't break serializer