# Detailed plan: Where to put `model` and `gpu_uuid` — per-row vs separate table
Branch: `plan/gpu-uuid-timeseries` (new, off main — never push/merge main)
Status: Planning only — no implementation until user approves. Updated with detailed option comparison.

## Two options (compared in detail)

### Option A: Per-row on `GPUMetric` (add `gpu_uuid` column; `model` already at line 73)
- Schema change: add `gpu_uuid` field directly to `GPUMetric` (same row as `gpu_index`, `gpu_util_pct`, etc.).
- DB impact: same table (`metrics_gpumetric`), same number of rows; `unique_together` (`rig_uuid`, `timestamp`, `gpu_index`) unchanged; new index on (`rig_uuid`, `gpu_index`, `gpu_uuid`, `-timestamp`) optional.
- Query pattern: `SELECT timestamp, gpu_index, gpu_uuid, model, gpu_util_pct FROM metrics_gpumetric WHERE rig_uuid=X` — single SELECT, no JOIN.
- Agent payload / serializer: array key = `gpu_index` (existing); serializer writes `gpu_uuid` + `model` into same row (new fields mapped from payload).
- Chart loader (`chart-runtime.js`): reads `gpu_index` (card slot) + `gpu_uuid` (identity) + `model` (label) from same fetched row array; no JOIN logic needed.
- Replacement detection: `SELECT timestamp, gpu_index, gpu_uuid FROM ... ORDER BY timestamp` grouped by index; compare UUID across time — single table.

### Option B: Separate table / model (`GPUProfile` or `GPUMetadata`), linked via (`rig_uuid`, `gpu_index`)
Proposed structure:
```
class GPUMetadata(models.Model):
    rig_uuid = models.UUIDField()
    gpu_index = models.PositiveSmallIntegerField()
    gpu_uuid = models.UUIDField()
    model = models.CharField(...)
    # maybe: from_timestamp, to_timestamp (occupancy period), replaced_by_uuid
    class Meta:
        unique_together = ('rig_uuid', 'gpu_index', 'from_timestamp', 'to_timestamp')  # or current-state
```
Then `GPUMetric` keeps `gpu_index`, links to metadata via (`rig_uuid`, `gpu_index`, `timestamp` range) or current-state lookup.

- Schema change: new table (`metrics_gpumetadata`); `GPUMetric` unchanged except possibly foreign key or no link at all (lookup by index + time).
- DB impact: extra table; extra JOIN or subquery for every chart/query that needs UUID/model.
- Query patterns (more complex):
  - Per-row with identity: `SELECT m.*, meta.gpu_uuid, meta.model FROM metrics_gpumetric m LEFT JOIN metrics_gpumetadata meta ON (m.rig_uuid = meta.rig_uuid AND m.gpu_index = meta.gpu_index AND meta.to_timestamp IS NULL) WHERE ...`
  - Replacement detection: compare UUID in metadata table over time windows (needs `from`/`to` tracking), not just `GPUMetric` row comparison.
- Agent payload / serializer: same payload format (`gpu_index` array with embedded UUID); but server writes UUID/model to metadata table instead of metric table. If current-state lookup: one insert/update to metadata; if time-range: insert with `from`/`to` and close previous.
- Chart loader: must JOIN or do separate fetch (metrics + metadata) to get UUID/model labels; more network/code complexity.
- Replacement detection requires time-range logic (`from`/`to`) in metadata table, because UUID is no longer in the metric row; without time-range you lose "when did UUID change at index X" directly from `GPUMetric`.

## Direct comparison (detailed — same criteria)

| Criteria | Option A (per-row on GPUMetric) | Option B (separate table, linked by index) |
|---|---|---|
| Schema change size | Small (add `gpu_uuid` column; `model` already exists line 73) | Large (new model + table + migration; link logic) |
| DB rows affected | Zero new rows; same metric rows gain 1-2 fields | New table with at least 1 row per GPU per rig (current state); potentially time-range rows |
| Query complexity (chart loader) | Single SELECT; no JOIN | JOIN or subquery (current-state lookup); or two separate fetches (metrics + metadata) |
| Replacement detection | Direct: compare `gpu_uuid` within same `gpu_index` across `GPUMetric` rows over time | Requires time-range tracking (`from`/`to`) in separate table; without it you lose direct comparison; with it, extra query complexity |
| Agent payload change | None (same array by index; serializer writes UUID into metric row) | None (same payload) but server writes to different table |
| DB constraint impact | `unique_together` (`rig_uuid`, `timestamp`, `gpu_index`) unchanged; can add index on UUID if needed | `GPUMetric` constraints unchanged; new `GPUMetadata` needs its own `unique_together` (index + current-state, or index + time-range) |
| Migration defense (W001, W004, 0052) | One migration (`*add_gpu_uuid*`); filter `0052_alter_*`; include `blank=True` if optional; add `checks.py` layer; `sync_to_opt.sh` filter | Two migrations (new table + foreign key/reference if any); same defense rules apply twice |
| Chart/loader regression | Zero: chart loader reads same endpoint, same array shape; just has extra fields available | Risk: loader must handle JOIN results or missing metadata (if no metadata row for index, UUID is null — empty series label) |
| Cross-rig same-GPU tracking | Possible (`SELECT ... WHERE gpu_uuid=X` across rigs) | Possible but requires JOIN or separate query on metadata table |
| Slot-level history (`GPUHistory`) derivation | Easy: derived query/grouping from metric rows (`group by gpu_index, gpu_uuid` over time windows) | Natural: metadata table IS the history table (if time-range); but then metric data and identity are split |
| Token / payload overhead | Minimal (one UUID string per GPU per minute) | Zero (UUID not in metric payload storage) — but separate table storage overhead instead |
| Code simplicity (per memory principle: simplify) | Higher: fewer models, fewer JOINs, single serializer write | Lower: extra model, extra table, extra lookup logic, potential JOIN bugs |

## Detailed gains explanation for Option A (per-row) — why it achieves the tracking goals
Because `gpu_index` stays (slot) and `gpu_uuid` is added (identity) in the same row:
- **Chart identity tracking**: loader fetches rows with both fields; groups series by UUID (identity continuity) while cards stay at index (slot). Replacement = UUID change at same index over time → clean series break.
- **Slot-level replacement detection**: `SELECT timestamp, gpu_index, gpu_uuid FROM metrics_gpumetric WHERE rig_uuid=X ORDER BY timestamp` — group by index, compare UUID. Know both *which slot* and *which card* changed.
- **Cross-rig comparison**: `SELECT * FROM metrics_gpumetric WHERE gpu_uuid=Y` — same physical card across different rigs/index positions.
- **Agent continuity check**: payload array by index includes UUID; server writes UUID to metric row; next ingest compares incoming UUID to last metric row UUID at same index — mismatch = replacement or corruption, flag for audit.
- **Derived occupancy history**: query `SELECT MIN(timestamp) as from_t, MAX(timestamp) as to_t, gpu_index, gpu_uuid, model FROM metrics_gpumetric WHERE rig_uuid=X GROUP BY gpu_index, gpu_uuid` — one derived history entry per card per slot. Only works because both index and UUID are co-located in metric rows.

## Detailed explanation for Option B — when it makes sense, and why it's more complex
Option B (separate table) makes sense IF:
- You want strict separation of identity/data (e.g., identity changes rarely, metrics change every minute) and you plan time-range tracking (`from_timestamp`, `to_timestamp`) for every GPU occupation.
- You want to enforce identity consistency via DB foreign key (e.g., `GPUMetric.gpu_uuid` as FK to `GPUMetadata.gpu_uuid`) — but then `GPUMetric` gains the UUID anyway (defeating the "separate" goal).
- You have many metrics and want identity metadata cached/reused independently.

Why it's more complex for this project:
- The chart loader and agent serializer already assume one table (`GPUMetric`) with array-by-index payload. Adding a second table requires either:
  1. JOIN in chart endpoint (slower, more DB load, risk of missing rows), OR
  2. Two separate endpoints (metrics + metadata) — loader must merge client-side.
- Replacement detection requires time-range logic (`from`/`to`) in metadata; without it you can't detect when UUID changed at a slot directly from `GPUMetric`.
- More migration/code/test overhead (defense-in-depth: W001, W004, 0052 applies to new model + migration + serializer + checks).

## Recommendation (for user decision)
- **Recommended**: Option A — add `gpu_uuid` directly to `GPUMetric` (line 73 `model` stays). Keeps `gpu_index` intact. Minimal change, achieves identity tracking, replacement detection, chart series break, agent continuity check, derived occupancy history — with zero JOINs and zero chart loader regression.
- **Only consider Option B** if user explicitly wants a separate identity/metadata model (e.g., for future foreign-key enforcement or strict time-range history table). Even then, Option A achieves the same tracking goals faster and with less risk.

## References
- `gpu_monitor/metrics_app/models.py`: `GPUMetric` lines 61-100 (`gpu_index` line 72, `model` line 73, `unique_together` line 93, index line 98).
- `agent_windows/run.py`: payload array format (index-keyed with embedded UUID if present).
- `gpu-rig-monitoring` skill: defense rules (W001, W004, 0052), chart loader patterns.
