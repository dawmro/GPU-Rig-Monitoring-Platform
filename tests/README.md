# Test scripts

This directory contains standalone Python scripts for verifying the
project's behavior. They are **not** Django unit tests (those live in
`gpu_monitor/*/tests.py`); they are smoke/integration tests that need
a real database connection or do raw file analysis.

## Running

All scripts can be run from the repo root (or any directory) using:

```bash
cd gpu_monitor
source venv/bin/activate
export DB_PASSWORD=local_dev_password
export DJANGO_ALLOWED_HOSTS='*'
python ../tests/<script_name>.py
```

The scripts share path constants via `tests/_paths.py`, which
defines `PROJECT_ROOT` and `GPU_MONITOR_DIR`. Each script adds the
parent `tests/` directory to `sys.path` and imports these constants,
so the project location is never hardcoded — the scripts work from
any checkout location.

## What's in here

| Script | What it tests | When to run |
|---|---|---|
| `test_layout_smoke.py` | All 7 main pages return HTTP 200 after a UI change | After any template or view change |
| `test_content_smoke.py` | Rendered HTML contains the expected `grm-` CSS classes; no old `text-red-400` / `bg-red-500` recipes remain | After CSS class changes |
| `test_sanity.py` | HTML tag balance (open/close), Django tag balance (if/endif) | After template changes |
| `_paths.py` | Shared `PROJECT_ROOT` and `GPU_MONITOR_DIR` constants; imported by all scripts | n/a (helper) |
| `test_chart_endpoints.py` | The chart-data API returns the right datasets and bucket counts for each metric | After chart-related view or model changes |
| `test_chart_logic.py` | View code itself: `ChartDataView` optimizations, fleet table inline-loop elimination, `power_total_kwh` derivation, compaction logic | After `metrics_app/views.py` or compaction changes |
| `test_live_metrics.py` | The `htmx-metrics` endpoint returns the right content (CPU card, etc.) | After `_fetch_rig_metrics` changes |

## History

These scripts were originally scattered at the repo root (`test_*.py`).
In November 2025, they were moved here and given path-agnostic imports.
The 4 newer scripts (`test_layout_smoke.py`, `test_content_smoke.py`,
`test_sanity.py`, `test_chart_logic.py`, `test_chart_endpoints.py`,
`test_live_metrics.py`) were created during the layout-optimization
refactor (Phase 0.1–0.4) to verify the template/CSS/JS extractions
didn't break the rendered output.
