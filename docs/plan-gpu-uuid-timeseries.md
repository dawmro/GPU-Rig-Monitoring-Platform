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

---

## GPU UUID/Model Change Tracking in Report Card

### Current Report Format Analysis

**Report endpoint**: `htmx_report_data` → `_build_report_context` → `_report_table.html`

**Data source for GPU section** (`views.py:715-736`):
```python
gpu_devices = GPUMetric.objects.filter(**base_filter)
    .values('gpu_index', 'model')
    .annotate(Avg/Max for metrics...)
    .order_by('gpu_index')
```

**Current GPU header** (`_report_table.html:4-5`):
```html
<h3>GPU {{ gpu.gpu_index }} — {{ gpu.model }}</h3>
```

**Gap**: No GPU UUID shown; no change detection for UUID/model over the report period.

---

### Proposed Enhancement: GPU Identity Change Tracking in Report

#### 1. Data Source Extension
Extend `_build_report_context` Query 1 to detect UUID/model changes per `gpu_index` within the report range:

```python
# New: Detect GPU identity changes per index within range
gpu_identity_changes = list(
    GPUMetric.objects.filter(**base_filter)
    .values('gpu_index')
    .annotate(
        # Get all distinct (uuid, model) combinations with first/last timestamps
        identities=ArrayAgg(
            Concat('gpu_uuid', Value('|'), 'model'),
            distinct=True,
            ordering=['timestamp']
        ),
        first_timestamp=Min('timestamp'),
        last_timestamp=Max('timestamp'),
    )
    .filter(identities__len__gt=1)  # Only indices with changes
)
```

Alternative: simpler approach using window functions or Python post-processing:
```python
# Fetch raw changes per index
gpu_raw = GPUMetric.objects.filter(**base_filter)
    .values('gpu_index', 'gpu_uuid', 'model', 'timestamp')
    .order_by('gpu_index', 'timestamp')

# Post-process: group by gpu_index, detect changes
changes_by_index = {}
for row in gpu_raw:
    idx = row['gpu_index']
    if idx not in changes_by_index:
        changes_by_index[idx] = []
    changes_by_index[idx].append({
        'uuid': row['gpu_uuid'],
        'model': row['model'],
        'timestamp': row['timestamp'],
    })

# Detect transitions
for idx, history in changes_by_index.items():
    prev = None
    for entry in history:
        if prev and (prev['uuid'] != entry['uuid'] or prev['model'] != entry['model']):
            # CHANGE DETECTED
            changes.append({
                'gpu_index': idx,
                'from_uuid': prev['uuid'],
                'from_model': prev['model'],
                'to_uuid': entry['uuid'],
                'to_model': entry['model'],
                'change_timestamp': entry['timestamp'],
            })
        prev = entry
```

#### 2. Report Context Extension
Add to `_build_report_context` return dict:
```python
return {
    ...
    'gpu_identity_changes': changes_list,  # List of dicts with change details
    'gpu_devices': gpu_devices,  # unchanged
    ...
}
```

Each change entry:
```python
{
    'gpu_index': 0,
    'from_uuid': 'a322cff7-...',  # or empty string
    'from_model': 'RTX 3060',
    'to_uuid': 'b676c04a-...',
    'to_model': 'RTX 4090',
    'change_timestamp': datetime(...),
}
```

#### 3. Template Changes (`_report_table.html`)

**Option A: Inline in GPU header** (minimal)
```html
<h3 class="text-sm font-medium text-gray-200 mt-4 mb-2">
    GPU {{ gpu.gpu_index }} — {{ gpu.model }}
    {% if gpu.uuid %}
        <span class="text-xs text-gray-400 font-mono ml-2">{{ gpu.uuid }}</span>
    {% endif %}
</h3>
```
*Note: Need to add `uuid` to `gpu_devices` query (add `gpu_uuid` to `.values()` and pass through)*

**Option B: Dedicated "GPU Changes" subsection** (comprehensive)
```html
{% if gpu_identity_changes %}
<div class="mt-6 p-4 bg-gray-800/50 rounded-lg">
    <h4 class="text-sm font-medium text-yellow-300 mb-3">GPU Identity Changes ({{ range_hours }}h)</h4>
    <div class="space-y-2 text-xs">
    {% for change in gpu_identity_changes %}
        <div class="flex flex-wrap gap-2 text-gray-300">
            <span class="font-mono text-gray-400">GPU {{ change.gpu_index }}:</span>
            <span class="text-red-400">{{ change.from_model }}</span>
            <span class="text-gray-500">→</span>
            <span class="text-green-400">{{ change.to_model }}</span>
            <span class="text-gray-500">@</span>
            <span class="font-mono">{{ change.change_timestamp|date:"M d H:i" }}</span>
            {% if change.from_uuid != change.to_uuid %}
                <span class="text-gray-500">(UUID: </span>
                <span class="font-mono text-red-400">{{ change.from_uuid|default:"—" }}</span>
                <span class="text-gray-500">→</span>
                <span class="font-mono text-green-400">{{ change.to_uuid|default:"—" }}</span>
                <span class="text-gray-500">)</span>
            {% endif %}
        </div>
    {% endfor %}
    </div>
</div>
{% endif %}
```

#### 3. Query Performance Considerations
- **Additional query**: 1 extra query per report (acceptable, ~4→5 queries)
- **Data volume**: Only scans `GPUMetric` in range (already queried for metrics)
- **Optimization**: Can combine with Query 1 by adding `gpu_uuid` to `.values()` and post-processing in Python
- **Caching**: Reuses existing `report_{uuid}_{range_hours}` cache key (55s TTL)

---

### Implementation Steps for Report Enhancement

1. **Modify Query 1** (`views.py:715-736`): Add `gpu_uuid` to `.values()` and include identity change detection
2. **Extend context** (`views.py:794-802`): Add `'gpu_identity_changes': changes_list`
3. **Update template** (`_report_table.html:4-5`): Show UUID in header + optional changes section
4. **Add fallback** for empty UUID: show `gpu-<index>` placeholder
5. **Test**: Verify with GPU replacement scenario (UUID change at same index)

---

### Summary of Report Changes

| Location | Change |
|----------|--------|
| `views.py:715-736` | Add `gpu_uuid` to `.values()`; add identity change detection logic |
| `views.py:794-802` | Add `'gpu_identity_changes'` to return dict |
| `_report_table.html:4-5` | Show UUID in GPU header; add "GPU Changes" subsection |
| Cache key | Unchanged (uses existing `report_{uuid}_{range_hours}`) |

---

### Benefits
- **Visibility**: Users see GPU UUID directly in report header
- **Audit trail**: Complete history of GPU swaps with timestamps
- **Correlation**: Link model changes to UUID changes (replacement vs reconfiguration)
- **No performance penalty**: Single extra query, cached at 55s TTL
- **Consistent with charts**: Same identity tracking logic as chart endpoint

---

### Future Enhancement (Post-MVP)
- Export changes to CSV/PDF for compliance
- Alert on unexpected GPU changes
- Cross-rig GPU tracking (same UUID seen on different rigs)
