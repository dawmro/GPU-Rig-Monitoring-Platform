---
name: gpu-rig-monitoring-extension-vast-env
version: 1.0
scope: class-level session reference for Vast.ai / NVIDIA env capture (2026-09-11)
---

# Vast.ai & NVIDIA Environment Variable Capture — Session Reference

## Why this extension exists
The `gpu-rig-monitoring` umbrella covers agent → server → template flow. This session produced a new pattern: filtering `os.environ` for `VAST_*` / `NVIDIA_*` and storing as JSON (no DB migration). Captured here as reference file rather than a new umbrella skill.

## Source facts verified this session
- Agent Linux (`agent/run.py` line 1171-1199): `collect_software()` — no env filter.
- Agent Windows (`agent_windows/run.py` line 1381-1411): same.
- Server (`metrics_app/serializers.py` line 482): `LatestSnapshot.software_json = software_data` (full dict, automatic storage).
- Template: Live Metrics tab (`rig_detail.html`) loads via HTMX (`htmx_metrics` → `_metrics_cards.html`).

## User's final snippet (adopted over earlier `vast_env` proposal)
```python
relevant_env = {}
for k, v in os.environ.items():
    if k.startswith('VAST_') or k.startswith('NVIDIA_'):
        relevant_env[k] = v
if relevant_env:
    result['relevant_env'] = relevant_env
```
Note: key is `relevant_env` (not `vast_env`) — includes NVIDIA, matches user's final correction.

## Plan (pending user approval per `writing-plans` skill)
- Step A: agent Linux (`agent/run.py` `collect_software()`)
- Step B: agent Windows (`agent_windows/run.py`)
- Step C: server (no change needed — `software_json` stores full dict)
- Step D: template — new card in Live Metrics tab (between System Info and Processes)

## Display recommendation
Live Metrics tab (`rig_detail.html`), compact card titled "Environment Variables (VAST / NVIDIA)", showing key-value pairs. Rationale: maps telemetry directly to Vast.ai billing instances (VAST_INSTANCE_ID, VAST_CONTAINERLABEL, VAST_TCP_PORT_*); NVIDIA variables (NVIDIA_VISIBLE_DEVICES, NVIDIA_DRIVER_CAPABILITIES) explain GPU container config.

## Defense / verification rules
- Filter: `k.startswith('VAST_') or k.startswith('NVIDIA_')`.
- Only writes when non-empty (`if relevant_env:`).
- Both Linux and Windows agents covered.
- No DB schema change → no migration; no `0052` defense needed; no compaction impact (stored in `LatestSnapshot.software_json`, not `MetricSnapshot`).

## Related umbrella skill
`gpu-rig-monitoring` (`SKILL.md` and `references/job-status-chart-plan.md`) — for agent/serializer/template flow, branch rules (`feat/<topic>`), defense-in-depth (code + system check + reference file).
