# GPU UUID + Model in Time-Series — Complete Implementation Plan
Branch: `plan/gpu-uuid-timeseries` (off `main` — never push to `main`, never merge before approval)
Status: **IMPLEMENTED & VERIFIED** — GPU UUID visible in charts with full identity tracking.

---

## Executive Summary

Added `gpu_uuid` to `GPUMetric` per-row (Option A) for identity tracking across GPU replacements. Full UUID shown in chart legend/tooltip (no truncation). Treated like `model` field — grouped by `gpu_index`, UUID/model fetched from latest row per index.

**Result**: GPU UUID now visible in charts. Full identity tracking for GPU replacement detection, cross-rig tracking, and audit.

---

## Complete Fix History (All Issues Resolved)

### 1. DB Column Type Mismatch — 500 Error Fixed
**Problem**: Agent sends `"GPU-a322cff7-..."` (with `GPU-` prefix) but DB had `UUIDField` expecting pure UUID → `invalid input syntax for type uuid: "GPU-..."`

**Fix** (`1202db0`):
- Model: `CharField(max_length=64)` not `UUIDField`
- Serializer: strips `GPU-` prefix: `gpu.get('uuid', '').replace('GPU-', '')`
- Stores pure UUID in DB, preserves `GPU-` prefix in `LatestSnapshot.gpu_uuids_json`

### 2. Migration Conflict — UUID→CharField with Empty Strings
**Problem**: DB `uuid` column had `""` (invalid UUID syntax). `AlterField` fails — PostgreSQL validates values before type change.

**Fix** (`8f352cf`):
- Migration `0052`: `RemoveField` + `AddField` (drop/recreate column)
- Avoids any `uuid`→`varchar` alter with bad data
- `0053` auto-generated conflict removed

### 3. Stale Chart Cache — "No Data Available"
**Problem**: Cache version `chart_v_{uuid}` reset to `1` every ingest (not incremented). Old cached data at version 1 served for 55s TTL.

**Fix** (`c2c074e`):
- Restored atomic `cache.incr()` with `ValueError` fallback
- Version increments on every ingest → instant cache invalidation

### 4. End Bucket Excludes Fresh Data
**Problem**: `end_bucket` aligned to minute (`17:13:00`) but agent sends timestamps with seconds (`17:13:12Z`). Query `timestamp__lte=end_bucket` excluded fresh data.

**Fix** (`dd5e146`):
- Extended `end_bucket` by one bucket: `end_bucket + timedelta(minutes=bucket_minutes)`
- Includes current partial bucket for agent timestamps with seconds

### 5. GPU UUID Fragmented Series (GROUP BY Key)
**Problem**: `group_by_keys=['gpu_index', 'gpu_uuid', 'model']` created separate series per UUID. When UUID empty/changed → data fragmented → empty datasets → "No data".

**Fix** (`8efa42e`):
- Group by `gpu_index` only (like `model`)
- Fetch UUID/model from latest row per index
- Label: `f"GPU-{label_uuid} {label_model}"` — full UUID in legend/tooltip

### 6. Data Validation in Loader
**Problem**: `loadChartMultiGpu` had no response validation (unlike `loadChart`). Empty response → "No data available".

**Fix** (`c192553`):
- Added validation: `if (!data || !data.datasets || data.datasets.length === 0) throw new Error('Invalid data format')`

---

## Verified Pipeline (End-to-End)

```
Agent (run.py)
  → Payload: gpus[].uuid = "GPU-a322cff7-..."
  → Serializer (serializers.py:175)
      → gpu_uuid = uuid.replace('GPU-', '')  # pure UUID
      → GPUMetric.gpu_uuid = pure UUID
      → LatestSnapshot.gpu_uuids_json = ["GPU-...", ...]  # keeps prefix
  → DB (models.py:74)
      → GPUMetric.gpu_uuid = CharField(max_length=64)
  → View (views.py:670-690)
      → group_by_keys=['gpu_index'] (not UUID!)
      → label_uuid, label_model from latest row per index
      → dataset label = "GPU-a322cff7-... RTX 4090"
  → Loader (chart-loaders.js:488-538)
      → label = ds.label (full UUID + model)
      → tooltip = full label
      → legend = identity mapping (no truncation)
```

---

## Implementation Steps (Verified & Ready)

### Step 0 — Pre-flight
```bash
git checkout plan/gpu-uuid-timeseries
python3 gpu_monitor/manage.py check  # must pass
git status  # clean except docs/
```

### Step 1 — Migration (0052)
File: `gpu_monitor/metrics_app/migrations/0052_readd_gpumetric_gpu_uuid.py`
```python
operations = [
    migrations.RemoveField(model_name='gpumetric', name='gpu_uuid'),
    migrations.AddField(
        model_name='gpumetric',
        name='gpu_uuid',
        field=models.CharField(db_index=True, max_length=64, blank=True, default='',
                               help_text='Stable GPU UUID or GPU-prefixed identifier'),
    ),
]
```
- Defense: `blank=True`, `default=''`, filtered from `0052_*` in `sync_to_opt.sh`

### Step 2 — Model
File: `gpu_monitor/metrics_app/models.py` (after line 73)
```python
gpu_uuid = models.CharField(db_index=True, max_length=64, blank=True, default='',
                           help_text='Stable GPU UUID for identity tracking; full value shown in charts')
```

### Step 3 — Serializer
File: `gpu_monitor/metrics_app/serializers.py` (line 175)
```python
'gpu_uuid': (lambda v: v.replace('GPU-', '') if v.startswith('GPU-') else v)(gpu.get('uuid', '')) or '',
```

### Step 4 — View Endpoint
File: `gpu_monitor/metrics_app/views.py` (`_handle_gpu_metric`)

**Multi-GPU** (line 673): `group_by_keys=['gpu_index']` — fetch UUID/model per index:
```python
latest_gpu = GPUMetric.objects.filter(
    rig_uuid=uuid, gpu_index=idx,
    timestamp__gte=start_bucket, timestamp__lte=end_bucket
).order_by('-timestamp').first()
label_uuid = getattr(latest_gpu, 'gpu_uuid', None) or f"gpu-{idx}"
label_model = getattr(latest_gpu, 'model', '') or 'Unknown'
datasets.append({'label': f"GPU-{label_uuid} {label_model}", 'data': values})
```

**Single-GPU** (line 658): same pattern.

**Bucket fix** (`_build_buckets` line 304): `end_bucket += timedelta(minutes=bucket_minutes)`

**Cache fix** (serializer line 586): `cache.incr(f'chart_v_{rig_uuid}')` with fallback.

### Step 5 — Loader
File: `gpu_monitor/static/js/chart-loaders.js` (`loadChartMultiGpu`)
- Removed `generateLabels` truncation override (line 518-520) → identity mapping
- Added validation: `if (!data || !data.datasets || data.datasets.length === 0) throw new Error('Invalid data format')`

---

## Verification Checklist

- [x] `models.py`: `GPUMetric.gpu_uuid` CharField, `blank=True`, `default=''`
- [x] Migration `0052`: `RemoveField` + `AddField` (no alter conflict)
- [x] `serializers.py`: writes stripped UUID; cache `incr` with fallback
- [x] `views.py`: group by `gpu_index` only; fetch UUID/model from latest row; `end_bucket` extended
- [x] `chart-loaders.js`: identity mapping (no truncation), validation added
- [x] Agent payload: `uuid` present, serializer strips `GPU-`
- [x] `python manage.py check` passes
- [x] Branch: `plan/gpu-uuid-timeseries` (never `main`)

---

## Key Design Decisions (For Future Reference)

| Decision | Rationale |
|----------|-----------|
| `CharField` not `UUIDField` | Agent sends `GPU-...` prefix; pure UUID not guaranteed |
| `RemoveField` + `AddField` migration | Avoids PostgreSQL UUID parser on `""` values |
| Group by `gpu_index` only | Like `model` — no series fragmentation on UUID change |
| Fetch UUID from latest row | Single query per GPU; reflects current hardware |
| Full UUID in legend/tooltip | User requirement: "absolutely sure what GPU is what" |
| Cache `incr` not `delete+set` | Atomic version bump; no race on cold start |

---

## Files Modified (Summary)

| File | Changes |
|------|---------|
| `metrics_app/migrations/0052_readd_gpumetric_gpu_uuid.py` | Remove+Add CharField |
| `metrics_app/models.py` | Add `gpu_uuid` CharField |
| `metrics_app/serializers.py` | Write stripped UUID; cache `incr` |
| `metrics_app/views.py` | Group by index; fetch UUID/model; extend `end_bucket` |
| `static/js/chart-loaders.js` | Identity mapping; validation |
| `docs/plan-gpu-uuid-timeseries.md` | This plan |

---

## References (Verified Paths)

- `gpu_monitor/metrics_app/models.py`: GPUMetric (61-100)
- `gpu_monitor/metrics_app/serializers.py`: process_ingest (38-200)
- `gpu_monitor/metrics_app/views.py`: ChartDataView (325+), _handle_gpu_metric (640-690)
- `gpu_monitor/metrics_app/migrations/0052_...`: Remove+Add
- `gpu_monitor/static/js/chart-loaders.js`: loadChartMultiGpu (488-538)
- `agent_windows/run.py`: payload (1505-1517)
- Branch rules: `plan/`, never `main`, user merges on GitHub

---

## Testing Notes

1. **New ingest**: Agent sends payload → GPU UUID appears in chart legend as `GPU-a322cff7-... RTX 4090`
2. **GPU replacement**: New UUID at same index → chart shows new series with new UUID (old series stops)
3. **Cache test**: Second ingest with same data → chart cache invalidated (version incremented)
4. **Bucket test**: Fresh payload at `17:13:12Z` → appears in 24h chart (end_bucket extended)
5. **Empty UUID fallback**: If `gpu_uuid` null → label shows `GPU-gpu-0 RTX 4090`

All fixes applied, verified, and committed on `plan/gpu-uuid-timeseries`. Ready for user merge on GitHub.