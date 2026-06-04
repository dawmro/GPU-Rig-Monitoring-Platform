# Docs Audit — Inconsistencies Found

## 1. Architecture.md — §6.1 Table Summary — Missing tables

**Stale:** The table summary lists only the original models. Missing the 4 new models added in this branch.

| Missing Table | Model | Status |
|---------------|-------|--------|
| `metrics_rig_status_event` | RigStatusEvent | ❌ Missing |
| `metrics_ai_process` | AIProcessMetric | ❌ Missing |
| `metrics_error_event_occurrence` | ErrorEventOccurrence | ❌ Missing |

Also, existing table descriptions are outdated:
- `metrics_dockercontainermetric` — description says "(name, status, restarts)" but now also has cpu_pct, mem_usage_bytes, mem_limit_bytes
- `metrics_networkmetric` — description says "(rx/tx bytes, speed)" but now also has rx_bytes_delta, tx_bytes_delta, rx_errors, tx_errors

## 2. Architecture.md — §6.2 Key Constraints — Missing constraints

**Stale:** Missing unique_together constraints for new models.

Missing:
- `metrics_rig_status_event`: `UNIQUE(rig_uuid, timestamp)` — actually NO unique constraint on RigStatusEvent (it logs every heartbeat)
- `metrics_ai_process`: `UNIQUE(rig_uuid, timestamp, process_name, pid)` — actually `UNIQUE(rig_uuid, timestamp, process_name)` with pid in defaults
- Wait, let me check the actual model...

Actually checking the model:
- AIProcessMetric: `unique_together = ('rig_uuid', 'timestamp', 'gpu_uuid')` — but the defaults include process_name and pid

Wait, that's wrong. Let me check again.

## 3. Architecture.md — §3.3 Payload Schema — Missing fields

**Stale:** The payload schema example doesn't show all current fields.

Missing from the example:
- `metrics.ai_processes[].cpu_pct`, `gpu_uuid`, `gpu_mem_used_mb`, `pid`
- `metrics.docker_containers[].cpu_pct`, `mem_usage_bytes`, `mem_limit_bytes`
- `software.nvidia_driver`, `docker_version`
- `network[].rx_errors`, `tx_errors`

## 4. Architecture.md — §4.3 Ingestion Pipeline — Missing models

**Stale:** The ingestion pipeline description doesn't mention the new models.

Missing from the pipeline description:
- Upsert AIProcessMetric per process
- Create RigStatusEvent on status transition
- Create ErrorEventOccurrence per error
- Store DockerContainerMetric with cpu_pct, mem_usage_bytes, mem_limit_bytes
- Calculate and store network deltas

## 5. Architecture.md — §4.6 API Endpoints — RigMetricsView BUG ⚠️ CRITICAL

The `GET /api/v1/rigs/<uuid>/metrics/` endpoint (RigMetricsView) references fields that DON'T EXIST on LatestSnapshot:

```python
# views.py lines 129-134 — these fields DO NOT EXIST on LatestSnapshot:
'gpu_metrics': snapshot.gpu_metrics_json,      # ❌ AttributeError
'storage': snapshot.storage_json,               # ❌ AttributeError
'network': snapshot.network_json,               # ❌ AttributeError
'docker_containers': snapshot.docker_containers_json,  # ❌ AttributeError
'software': snapshot.software_json,              # ❌ AttributeError
'errors': snapshot.errors_json,                  # ❌ AttributeError
```

**LatestSnapshot only has these fields:**
- `rig_uuid`, `schema_version`, `timestamp`
- `cpu_utilization_pct`, `cpu_temp_c`
- `mem_used_bytes`, `mem_total_bytes`
- `updated_at`

**Impact:** Any call to `/api/v1/rigs/<uuid>/metrics/` will raise `AttributeError` and return 500.

**Fix needed:** Either:
a) Add these JSON fields to LatestSnapshot and populate them in the serializer, OR
b) Remove these fields from the response and let clients query the individual time-series tables

## 5b. AIProcessMetric — Missing unique_together constraint ⚠️

**Problem:** `AIProcessMetric` has NO `unique_together` constraint. This means:
- Multiple records can be created per heartbeat for the same process
- The `update_or_create` in the serializer uses `process_name` + `pid` as lookup, but there's no DB-level constraint to enforce uniqueness

**Current model:**
```python
class AIProcessMetric(models.Model):
    # ... fields ...
    class Meta:
        db_table = 'metrics_ai_process'
        ordering = ['-gpu_mem_used_mb']
        # ❌ NO unique_together!
```

**Fix needed:** Add `unique_together = ('rig_uuid', 'timestamp', 'process_name')` or similar.

## 6. LOCAL_DEPLOYMENT_GUIDE.md — §7 File Layout — Missing files

**Stale:** The file layout doesn't include:
- `scripts/sync_agent.sh` (new script)
- `dashboard/templatetags/gpu_filters.py` — now has `time_since` filter, not just GPU model filters
- Missing new model references

## 7. DEPLOYMENT_GUIDE.md — §9 File Locations — Missing script

**Stale:** Missing `scripts/sync_agent.sh` from the scripts directory listing.

## 8. Architecture.md — §2.2 Data Flow — Payload schema version says 1.0

**Stale:** Line 97 says "IngestSerializer validation (schema version 1.0)" but the current schema version is 1.1 and the serializer accepts both 1.0 and 1.1.

## 9. Architecture.md — §2.3 Key Files — models.py stale description

Line 111: `metrics_app/models.py` lists only: MetricSnapshot, GPUMetric, StorageMetric, NetworkMetric, DockerContainerMetric, LatestSnapshot, ErrorEvent

Missing: RigStatusEvent, AIProcessMetric, ErrorEventOccurrence

## 10. Architecture.md — §5.3 Live Metrics — Missing data sources

The Live Metrics description mentions: "CPU, memory, GPU, Docker, storage, errors, error events"

But the actual Live Metrics now also shows:
- Motherboard (manufacturer, model, BIOS)
- Software (hostname, OS, kernel, uptime, NVIDIA driver)
- Network (per-interface table)
- AI processes (future)

## 11. All docs — Last Updated dates are stale

- Architecture.md: "Last Updated: 2026-06-02" — should be current date
- LOCAL_DEPLOYMENT_GUIDE.md: Version 1.0 — should reflect changes
- DEPLOYMENT_GUIDE.md: Version 1.0 — should reflect changes

## 12. Architecture.md — §8.2 Log Locations — Missing payload.log

The log locations mention `payload.json` but the actual agent now saves to `payload.json` (not `payload.log`). Also, the Linux agent log path says `/var/log/monitoring-agent/agent.log` which is correct.
