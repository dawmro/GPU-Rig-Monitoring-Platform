# Static JS

This directory holds JavaScript files extracted from inline `<script>`
blocks during Phase 0.4 of the layout-optimization refactor.

## Files

| File | Loaded by | Purpose |
|---|---|---|
| `app-base.js` | base.html | Page-wide utilities: clocks, mobile menu, email toggle |
| `chart-base.js` | rig_detail.html | Shared Chart.js options (scales, ticks, tooltip) — replaces 6 verbatim copies |
| `chart-colors.js` | rig_detail.html | Centralized chart color palettes (GPU, loadavg, memory, network) — replaces 4 inline palettes |
| `chart-loaders.js` | rig_detail.html | 7 chart data loaders (loadChart, loadChartMultiGpu, etc.) — replaces 7 inline functions |
| `chart-runtime.js` | rig_detail.html | Chart loader orchestrator and global state (loadCharts, setChartRange) |
| `rig-detail.js` | rig_detail.html | Page-level interactions: tab switching, modal, rename, report range |

## Globals

All files attach to `window.GRM.*` (no module loader required):

- `GRM.AppBase` — page utilities (`init()`, `initClocks()`, `toggleAllOwnerEmails()`)
- `GRM.ChartBase` — chart options (`xAxisOptions()`, `yAxisOptions()`, `baseOptions()`, etc.)
- `GRM.ChartColors` — color palettes (`GPU_COLORS`, `LOADAVG`, `MEM`, `NETWORK`, `colorAt()`)
- `GRM.ChartLoaders` — chart loaders (`loadChart`, `loadChartMultiGpu`, etc.)
- `GRM.ChartRuntime` — chart state and orchestration (`loadCharts()`, `setChartRange()`)
- `GRM.RigState` — per-page state (set by the rig_detail.html inline script):
  - `rigUuid`: the rig's UUID
  - `reportUrl`: URL for the report range endpoint
  - `reportLoaded`: bool, set after first report load

`rig-detail.js` also exposes a few functions directly on `window`
(`switchTab`, `selectReportRange`, `toggleRenameForm`, `showDeleteModal`,
`hideDeleteModal`, `setChartRange`) so the inline `onclick="..."`
attributes in `rig_detail.html` don't need to be rewritten.

## Load order

The files must be loaded in this order (the load order is defined in
`base.html` and `rig_detail.html`):

1. `app-base.js` (in base.html, all pages)
2. `chart-base.js` (in rig_detail.html, deps on nothing)
3. `chart-colors.js` (in rig_detail.html, deps on nothing)
4. `chart-loaders.js` (in rig_detail.html, deps on chart-base.js + chart-colors.js)
5. `chart-runtime.js` (in rig_detail.html, deps on chart-loaders.js)
6. `rig-detail.js` (in rig_detail.html, deps on chart-runtime.js + GRM.RigState)

The Django system check (`dashboard.checks.check_templates_reference_js_files`)
verifies all 6 files are referenced from the correct templates. If
a file is missing, the check raises an Error (`E002` for missing
source, `W003` for missing reference in template).

## How the chart system fits together

```
   rig_detail.html
        │ {% include %} chart canvas elements
        ▼
   chart-runtime.js
        │ calls loadCharts(uuid) on Charts tab
        │ staggered, 100ms apart
        ▼
   chart-loaders.js
        │ each loader is one line in the buildLoaders() registry
        │ loadChart, loadChartMultiGpu, loadChartMemSwap, etc.
        ▼
   chart-base.js (for shared options)
   chart-colors.js (for color palettes)
        ▼
   Chart.js (CDN) — renders the canvas
```

The data flow: `chart-loaders.js` calls `fetch('/api/v1/rigs/UUID/chart-data/?...')`,
parses the JSON, builds a Chart.js dataset, and uses `chart-base.baseOptions()`
to construct the options object (which includes the shared scales, ticks,
tooltip, and interaction config). The colors come from `chart-colors.GPU_COLORS`
(cycled for multi-series) or one of the per-chart palettes (loadavg, mem, network).

## Why no bundler

We use vanilla JS with IIFE wrappers and `window.GRM.*` namespacing.
No build step, no module loader, no transpilation. The files are loaded
as plain `<script src="...">` tags in document order. This is the
right trade-off for a project of this size — the entire JS payload
(including Chart.js from CDN) is ~5 KB for the chart utility files.
A bundler would add complexity for no win.
