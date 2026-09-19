# Chart Routes / JS Refactor Plan — Self-Contained Feature Plan
Branch: `refactor/chart-routes-js` (from pulled `main`). Each feature = one self-contained commit. Never push `main`; user merges.

---

## Feature 1 — Loader Unit Tests (Self-Contained; No Production Code Change)
**Commit message:** `feat(test): loader unit tests — verify loadChartMultiGpu returns Promise, dataset labels match contract`
**Files:** `tests/test_chart_loaders_unit.py` (new only)
- Mocks `window.GRM.ChartBase` / `Colors`.
- Calls `loadChartMultiGpu('canvas', 'gpu_util_pct', 'uuid', 24, '%')`.
- Asserts: returns Promise; dataset label format; no syntax errors.
- Independent: no change to loader/template/code; safe to merge/revert alone.
- Verification: `python -m pytest tests/test_chart_loaders_unit.py` (or manual if no venv).

---

## Feature 2 — Consolidate `generateLabels` (Self-Contained; Loader Only)
**Commit message:** `feat(refactor): consolidate generateLabels — extract to Base.generateLabels, remove 6 inline copies`
**Files:** `gpu_monitor/static/js/chart-base.js`, `gpu_monitor/static/js/chart-loaders.js`
- Add `Base.generateLabels(config)` in `chart-base.js` (handles UUID truncation + interface truncation as parameterized options).
- Replace all 6 inline `generateLabels` blocks in `chart-loaders.js` with `Base.legendOptions({ generateLabels: Base.generateLabels })`.
- No template/view change; behavior identical (legend truncation preserved); DRY enforced.
- Verification: `node --syntax-only` or manual brace balance; loader exports verified.

---

## Feature 3 — Refactor `loadChartNetworkCombined` (Self-Contained; Loader Only)
**Commit message:** `feat(refactor): split loadChartNetworkCombined — fetch + build + render as composable functions`
**File:** `gpu_monitor/static/js/chart-loaders.js` (network section only, lines 200–340 area)
- `fetchNetworkData(url)`: Promise.all for RX/TX/Errors.
- `buildNetworkDatasets(rxData, txData, errData)`: maps to dataset objects (yAxisID, barThickness, borderDash).
- `renderNetworkChart(ctx, labels, datasets)`: creates Chart instance.
- Old inline block removed; new helper functions added at bottom or in same file.
- No template/view/runtime change; same 22-chart registry.
- Verification: syntax check; loader exports unchanged (`loadChartNetworkCombined` still present).

---

## Feature 4 — Simplify `loadCharts()` Staggering (Self-Contained; Runtime Only)
**Commit message:** `feat(refactor): simplify loadCharts staggering — replace sequential Promise chain with map + Promise.all`
**File:** `gpu_monitor/static/js/chart-runtime.js`
- Replace `p = p.then(...)` chain (lines 90–99) with `Promise.all()` or `loaders.map(...).map(...)` with 100ms delay preserved.
- Same 2.2s total spread; same `delayedLoad` helper; `state` unchanged.
- No loader/template/view change.
- Verification: runtime exports (`loadCharts`, `setChartRange`, `labelForRange`) unchanged.

---

## Feature 5 — Extract Loader Registry (Self-Contained; Config Module)
**Commit message:** `feat(refactor): extract loader registry to chart-registry.js — separates chart definitions from loader functions`
**Files:** `gpu_monitor/static/js/chart-registry.js` (new), `gpu_monitor/static/js/chart-runtime.js` (updated), `gpu_monitor/templates/dashboard/rig_detail.html` (updated if needed)
- Registry array: `[{ id: 'chartGpuTemp', loaderFn, metric, ... }, ...]`.
- `chart-runtime.js`: reads registry; `buildLoaders()` generates loader array from registry.
- `rig_detail.html`: loader tag unchanged (`?v=2` kept from previous fix if still present); no HTML order dependency.
- Independent: can revert registry file without affecting loader/runtime logic (separation of concerns).
- Verification: `loadChartMultiGpu` still called; same 22 charts loaded; no 500.

---

## Feature 6 — Refactor `app-base.js` Clock/Update Logic (Self-Contained; Base Only)
**Commit message:** `feat(refactor): replace fragile HTMX swap listener with direct selector approach`
**File:** `gpu_monitor/static/js/app-base.js`
- Replace `evt.detail.target.id` dependency (lines 53–62) with `document.getElementById('global-refresh-clock')` (same as `initClocks`).
- `updateClocksOnHtmxSwap()` simplified: no event-detail parsing; same behavior (updates clock on swap).
- No loader/template change; independent from chart pipeline.
- Verification: mobile menu (`initMobileMenu`) unchanged; clock updates work.

---

## Cross-Feature Rules (Per User Requirement: Self-Contained)

- Each feature: one commit, one branch (`refactor/chart-routes-js`), never `main`.
- Each commit message: `feat(refactor): <description>` or `fix/refactor: ...`.
- Each feature verified independently (syntax + loader presence + no 500) before next.
- If any feature fails, revert that single commit (`git revert <sha>`); others unaffected.
- Push to branch: `git push origin refactor/chart-routes-js` (not `main`).
- User merges on GitHub after reviewing each feature.
