# Layout & Code Optimization Plan

**Branch:** `plan/layout-optimization`
**Status:** Draft — awaiting user approval before any implementation
**Date:** 2026-09-07

This plan covers three tightly related goals:

1. **Readability** — make Fleet Overview and Rig Detail easier to scan on first look
2. **Code simplification** — collapse repeated Tailwind patterns into reusable partials/CSS, and collapse repeated HTMX/JS patterns into reusable utilities
3. **Maintainability** — reduce the size of `rig_detail.html` (currently 1441 lines, the largest file in the codebase) by extracting JS and reusable markup into named partials/assets

---

## 1. Findings from analysis

### 1.1 Inventory (what exists today)

| Area | Files | Total LOC |
|---|---|---|
| Templates (HTML) | 27 | ~3,980 |
| Views (Python) | 4 modules | ~1,229 |
| Templatetags (Python) | 1 (`gpu_filters.py`) | 436 |
| Models (Python) | 4 modules | — |

**Largest files (real LOC, excluding migrations/venv/tests):**

| File | LOC | Notes |
|---|---|---|
| `templates/dashboard/rig_detail.html` | **1,441** | Includes ~900 lines of inline JS for charts + tabs + modal + clock |
| `templates/dashboard/_metrics_cards.html` | 942 | Long, but mostly straightforward markup |
| `templates/dashboard/views.py` | 778 | `_fetch_rig_metrics` alone is ~200 lines |
| `templates/dashboard/templatetags/gpu_filters.py` | 436 | 3 near-identical `gpu_*_cell_json` simple_tags |
| `templates/base.html` | 160 | OK, but the inline JS at the bottom is growing |
| `templates/dashboard/_rig_table.html` | 160 | OK |

### 1.2 Tailwind — duplication patterns

The same Tailwind recipe is repeated dozens of times across templates:

**Card panel** (`bg-gray-800 border border-gray-700 rounded-lg p-4` or `p-6`)
- Used **40+ times** across `_metrics_cards.html` (CPU/Power/Memory/Storage/GPU/GPU Processes/Top Processes/Process Details), `rig_detail.html` (Containers/Errors/Report containers), `profile.html` (3 panels), `api_keys.html` rows, `admin_transfer_keys.html` steps, `audit_log.html` log entries.

**Form input** (`bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white` or `px-3 py-2`)
- Used **15+ times** across search/filter inputs, tag inputs, login/profile/admin forms.

**Primary action button** (`bg-blue-600 hover:bg-blue-700 text-white font-medium px-4 py-2 rounded` or similar)
- Used **10+ times** in login, profile, api_keys, admin_transfer_keys, etc.

**Danger button** (`bg-red-600 hover:bg-red-500 text-white ...`)
- Used in delete-modal, revoke key, etc.

**5-tier color thresholds** are duplicated as inline `{% if %}…{% elif %}…{% else %}…` chains **inside templates** for CPU temp / GPU temp / GPU fan / CPU util / Disk util / Mem util / Power draw — see §1.5.

**Color palette** is duplicated across `base.html` CSS (status badges), `_metrics_cards.html` (`bg-red-500`/`bg-yellow-500`/`bg-green-500` progress bars), `gpu_filters.py` (`text-red-400`/`text-yellow-400`/...), and inline in JS (`rgba(239, 68, 68, 0.8)` etc. in `rig_detail.html`).

### 1.3 HTMX — duplication patterns

Three control flow patterns recur:

**A. Polling div with indicator** — every polling partial has the same shape:
```html
<div id="..." hx-get="..." hx-trigger="every 30s" hx-target="#..." hx-swap="innerHTML" hx-indicator=".htmx-indicator">
  {% include "..." %}
</div>
```
Used for: `rig-table-container` (30s), `rig-status-container` (15s), `metrics-container` (30s).

**B. Search input with debounced keyup + filter selects** — all three filters in `rig_list.html` repeat `hx-trigger="keyup changed delay:500ms"`, `hx-get`, `hx-target`, `hx-include` verbatim. The 3 controls each re-declare `hx-include="[name='search'], [name='status'], [name='tag']"` in different subsets.

**C. Form with CSRF + hx-post + swap into row** — used in `_key_row.html`, `api_keys.html` (revoke/reactivate/delete), `rig_detail.html` (rename), `audit_log.html` (none, but adjacent). Each repeats `{% csrf_token %}` and identical hx-* attrs.

### 1.4 JavaScript — duplication patterns

`rig_detail.html` is essentially a JS app inline in HTML:

| JS block | LOC | Repetition |
|---|---|---|
| `loadChart` | ~90 | 7 copies of nearly identical "create Chart instance with same options object" boilerplate (single line/multi-gpu/multi-key/dual-metric/load-avg/mem-swap/network) |
| Chart `scales.x` + `scales.y` + `interaction` options | ~20 | Repeated **6 times verbatim** across `loadChart`, `loadChartMultiGpu`, `loadChartMultiKey`, `loadChartMultiKeyDual`, `loadChartLoadAvg`, `loadChartMemSwap`, `loadChartNetworkCombined` |
| Tooltip callback | ~5 | Repeated **6 times verbatim** |
| `generateLabels` UUID/key truncation | ~15 | 3 near-copies (multi-GPU, multi-key, network combined) |
| GPU_COLORS / NET_COLORS / memColors / loadAvgColors | ~30 | 4 separate color palettes, defined inline in JS |
| Tab switching + range button highlight | ~50 | 2 near-identical "update all buttons to inactive then activate selected" loops |
| Modal show/hide | ~25 | Could be replaced with the native `<dialog>` element |

### 1.5 Color threshold logic — duplication

The same "5-tier color threshold" is implemented **at least 6 times** in 3 different languages:

| Where | Metric | Thresholds |
|---|---|---|
| `gpu_filters.py` `cpu_util_color` | CPU util | >80 red, >60 orange, >40 yellow, >20 green, else gray |
| `gpu_filters.py` `gpu_temp_cell_json` | GPU temp | >80 red, >75 orange, >70 yellow, >65 green, else gray |
| `gpu_filters.py` `gpu_util_cell_json` | GPU util | >90 green, >50 gray-300, else gray-500 |
| `gpu_filters.py` `gpu_fan_cell_json` | GPU fan | >80 red, >60 yellow, else gray |
| `_rig_table.html` inline | CPU temp | >85 red, >70 yellow, else green |
| `_rig_table.html` inline | Disk util | ≥80 red, ≥60 orange, ≥40 yellow, ≥20 green, else gray |
| `_metrics_cards.html` inline | CPU util progress | >80 red, >60 yellow, else green |
| `_metrics_cards.html` inline | CPU temp | >85 red, >70 yellow, else green |
| `_metrics_cards.html` inline | Mem util | >85 red, >70 yellow, else blue |
| `_metrics_cards.html` inline | Storage usage | >90 red, >75 yellow, else blue |
| `_metrics_cards.html` inline | GPU temp | >80 red, >70 yellow, else green |
| `_metrics_cards.html` inline | GPU util | >90 red, >70 yellow, else green |
| `_metrics_cards.html` inline | Mem controller util | >90 red, >70 yellow, else green |
| `_metrics_cards.html` inline | Disk util | ≥80 red, ≥60 orange, ≥40 yellow, ≥20 green, else gray |
| `base.html` inline JS | Status badge | online=green, stale=amber, offline=red |

**Inconsistencies** between copies (these are bugs hiding in plain sight):
- CPU temp: 85/70 vs 80/70 across files
- GPU temp: 80/75/70/65 in `gpu_filters.py` vs 80/70 in templates
- GPU util: 90/50 in `gpu_filters.py` vs 90/70 in `_metrics_cards.html`
- Disk util: identical thresholds (≥80/≥60/≥40/≥20) — only place that matches
- Mem util: 85/70 in `_metrics_cards.html` vs no coloring anywhere else

A single source of truth (one Python helper returning the class, or one CSS data-attribute-based scheme) would fix this.

### 1.6 Readability issues (visual scan)

**Fleet Overview (`_rig_table.html`)**
- 15 columns, all squeezed into one row with `text-xs` and `whitespace-nowrap`. On a 1920px monitor it's tight; on a laptop it's horizontally scrolling.
- GPU Temp / Fan / Util columns display space-separated multi-GPU values (`75 78 72`) with no visual separator between values — easy to misread.
- Status column has 3 nearly identical `<span>` blocks differing only by class. Could be one partial.
- `cpu_util_color` is applied; disk util has the same threshold set inline. Inconsistent: same colors, different code path.

**Rig Detail (`rig_detail.html`)**
- Tab order is implicit (HTML order). 5 tabs × ~20 charts each = a huge "Charts" tab that takes 10+ scrolls. No visual grouping of related charts (e.g. all GPU charts together, all CPU charts together, all Disk charts together — there IS grouping in HTML, but it's not visually distinct).
- The "Charts" tab is a wall of `<div class="bg-gray-800 … h-[280px]"><h3>…</h3><canvas></div>` repeated ~20 times. Could be a small `_chart_card.html` partial.
- Tab nav has 5 buttons + a hidden chart-timeframe picker. On mobile, the timeframe picker appears below the tabs but is `hidden` until Charts tab is active — slightly confusing.
- "Process Details" and "Top Processes" cards in `_metrics_cards.html` show very similar data — "Top Processes" is the at-a-glance list (PID/Name/CPU%/Mem%/User), "Process Details" is the expanded one (PID/Name/Command). Could be one card with two views.

**Containers / Errors tabs** are nearly identical in structure — list of cards, scroll, empty state. Two separate code paths doing the same thing.

**Live Metrics card stack** — CPU, Power, Memory, Storage, GPU, GPU Processes, Top Processes, Process Details. 8 cards stacked vertically with no visual hierarchy (every card is the same shape). No quick summary at the top ("All systems normal" / "1 disk failing" / etc.).

### 1.7 Maintainability issues

1. **`rig_detail.html` mixes 3 concerns**: (a) page layout, (b) HTMX wiring, (c) ~900 lines of JS for charts. Editing any one of these is risky because they all live in the same file.
2. **`_metrics_cards.html`** is 942 lines because it inlines 8 large sections. No partials — copy/paste between CPU/GPU/Storage progress bars.
3. **`gpu_filters.py`** has 3 near-identical `gpu_*_cell_json` simple_tags. The only difference is the threshold bands. Could be one helper called with a thresholds table.
4. **`_fetch_rig_metrics` in `views.py`** is ~200 lines and builds 5 different dict structures (gpu/storage/network/processes/docker) with the same `_json_get(snapshot.X, i)` pattern repeated dozens of times.
5. **`base.html`** has growing inline JS for mobile menu + clock + email toggle. Adding more global JS will make it unmanageable. A `static/js/app.js` file with namespace `window.GRM = {...}` is overdue.

### 1.8 Color palette — inconsistency

- "online" badge: `bg-green-900/50 text-green-300` in one place, hardcoded `#065f46/#6ee7b7` in `base.html` CSS
- "stale" badge: `#78350f/#fcd34d` (CSS) vs `bg-yellow-900/50 text-yellow-300` (templates)
- "offline" badge: `#7f1d1d/#fca5a5` (CSS) vs `bg-red-900/50 text-red-300` (templates)
- The "status" badge in `base.html` uses **different colors** than the status badge in `_rig_table.html` for the same `online/stale/offline` value. Inconsistency.

---

## 2. Plan — phased rollout

### Phase 0: Foundation (no behavior change)

> All work in this phase is pure refactor. Zero visible difference to the user.

**0.1 Move all shared CSS classes to `static/css/app.css`**

Add a small CSS layer (kept under `static/css/`, not inline) with named utility classes:

```css
/* Card panel — was repeated 40+ times */
.grm-card        { @apply bg-gray-800 border border-gray-700 rounded-lg p-4; }
.grm-card-lg     { @apply bg-gray-800 border border-gray-700 rounded-lg p-6; }

/* Form controls */
.grm-input       { @apply bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white; }
.grm-input-lg    { @apply bg-gray-700 border border-gray-600 rounded px-3 py-2 text-white; }
.grm-select      { @apply bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white; }

/* Buttons */
.grm-btn-primary { @apply bg-blue-600 hover:bg-blue-700 text-white font-medium px-4 py-2 rounded transition; }
.grm-btn-danger  { @apply bg-red-600 hover:bg-red-500 text-white rounded text-sm font-medium px-4 py-2; }
.grm-btn-ghost   { @apply bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-sm px-4 py-2; }

/* Status badges — single source of truth */
.grm-badge-online  { @apply px-1.5 py-0.5 rounded text-xs bg-green-900/50 text-green-300; }
.grm-badge-stale   { @apply px-1.5 py-0.5 rounded text-xs bg-yellow-900/50 text-yellow-300; }
.grm-badge-offline { @apply px-1.5 py-0.5 rounded text-xs bg-red-900/50 text-red-300; }

/* Progress bar wrapper + fill — used 6+ times in _metrics_cards.html */
.grm-progress-track { @apply w-full bg-gray-700 rounded-full h-2; }
.grm-progress-fill  { @apply h-2 rounded-full; }

/* 5-tier color thresholds — see §0.3 */
.grm-tier-red    { @apply text-red-400; }
.grm-tier-orange { @apply text-orange-400; }
.grm-tier-yellow { @apply text-yellow-400; }
.grm-tier-green  { @apply text-green-400; }
.grm-tier-gray   { @apply text-gray-500; }
```

Then replace every duplicated instance with the new class. Estimated reduction: ~400 lines across templates.

**0.2 Create the 3 reusable template partials**

```
templates/partials/_card.html       — accepts title, content, optional footer
templates/partials/_form_input.html — accepts name, type, label, value, placeholder
templates/partials/_chart_card.html — accepts canvas_id, title, height
```

Examples:
```django
{% include "partials/_card.html" with title="CPU" content=... %}
{% include "partials/_chart_card.html" with canvas_id="chartGpuTemp" title="GPU Temperature" %}
```

**0.3 Unify 5-tier color thresholds**

Add one Python helper in `templatetags/gpu_filters.py`:

```python
def color_tier(value, thresholds, default='gray'):
    """Single source of truth for 5-tier color thresholds.
    
    thresholds: list of (min_value, color_name) tuples, highest first.
    color_name: 'red' | 'orange' | 'yellow' | 'green' | 'gray'
    """
```

Usage: `{{ value|color_tier:cpu_temp_thresholds }}` where `cpu_temp_thresholds` is a context variable set once per page (e.g. via `{% get_thresholds as cpu_temp_thresholds %}`).

This collapses 14 inline `{% if %}…{% elif %}…{% else %}…` chains into 14 single-line `{{ x|color_tier:y }}` calls.

**0.4 Extract JavaScript out of `rig_detail.html`**

Move all JS into named modules under `static/js/`:

```
static/js/
├── app.js                 — global utilities (mobile menu, clock, csrf helper)
├── chart-base.js          — shared Chart.js options (scales, tooltip, interaction)
├── chart-loaders.js       — the 7 loadChart* functions (now thin wrappers over chart-base)
├── chart-colors.js        — GPU_COLORS, NET_COLORS, memColors, loadAvgColors
├── rig-detail-tabs.js     — tab switching, range button highlight, modal
└── rig-list.js            — toggle owner emails, polling refresh clock
```

Each file uses an IIFE with `window.GRM = window.GRM || {}` to avoid globals pollution. `rig_detail.html` shrinks from 1441 → ~600 lines.

### Phase 1: Readability (visual, no behavior change)

**1.1 Fleet Overview — column grouping**

Add visual section headers above the table by splitting the row into 3 logical groups:

```
[ Identity ]   [ Status ]      [ GPU block ]                              [ System ]              [ Meta ]
Rig | Tags | Status | Job | Last | Uptime | GPU | Temp | Fan | Util | CPU-T | CPU% | Disk | Power | Agent
```

Use `<colgroup>` + `<col class="…">` to add subtle background tint to each group, OR keep flat but tighten column widths.

Trade-off: <colgroup> styling is widely supported and pure HTML/CSS — no JS needed.

**1.2 Multi-GPU cells — clear value separators**

Current: `75 78 72` (just spaces) — visually ambiguous with single-GPU values like `75`.

Change to: pipe-separated with a softer separator (CSS only):
```css
.grm-multi-value > span + span::before { content: " · "; color: #4b5563; }
```
Output: `75 · 78 · 72`. ~3px wider per GPU, but unambiguous.

**1.3 Fleet Overview — collapsible row expansion**

Add an "expand" button per row that reveals 4 extra metrics that don't fit on the default view (e.g. CPU temp, CPU freq, network summary, agent uptime). Hidden by default at all viewport widths. Pure HTMX/JS, no DB change.

Trade-off: adds 1 click for power users but makes the table actually readable at 1366×768.

**1.4 Rig Detail — "at-a-glance" summary card at the top of Live Metrics**

Add a thin summary bar (no card chrome) above the metric cards showing the most important signals: # online GPUs, # disks >80% full, # recent errors, total power. Reduces the "where do I look" problem.

**1.5 Charts tab — sticky section headers**

Inside the Charts tab, add a sticky sub-nav with section anchors: GPU | CPU | Disk | Memory | Network | Other. Click jumps to section. Pure CSS (`position: sticky`).

**1.6 Containers / Errors tabs — unify into one "Events" tab**

Both tabs are conceptually "things that happened". Merge them into a single tab with a filter toggle (Containers / Errors / Both). Saves 1 tab slot for future use.

**1.7 Live Metrics card hierarchy**

Top of Live Metrics: 1 thin "system health" bar (green/yellow/red dot + 1-line summary like "8 GPUs · 1243W · 3 disks OK · no errors").

Below: the existing 8 cards, but in 2 columns on `lg:` screens. The user gets an overview without scrolling 3 pages.

### Phase 2: Code simplification (no behavior change)

**2.1 Collapse `gpu_*_cell_json` into one helper**

Replace 3 functions (gpu_temp_cell_json, gpu_util_cell_json, gpu_fan_cell_json) with one:

```python
@register.simple_tag
def gpu_metric_cell_json(snapshot, json_field, thresholds, suffix='', fmt='.0f'):
    """Generic color-coded multi-GPU cell from a JSON array on LatestSnapshot."""
```

**2.2 Collapse `loadChart*` JS functions**

Replace 7 chart loader functions with one parameterized function:

```javascript
window.GRM.charts.load({
    canvasId, metric, range, unit, type, // 'single' | 'multi-gpu' | 'multi-key' | 'dual'
    multiParam, colorBorder, colorBg, extraMetric, ...
});
```

Estimated reduction: ~600 lines of JS.

**2.3 Status badge consolidation**

Replace 5+ status badge renderers (3 in `_rig_table.html`, 2 in `base.html` CSS, 1 in `_rig_status_badge.html`) with one `_status_badge.html` partial. Use the unified color classes from §0.1.

**2.4 `_fetch_rig_metrics` — extract per-device builders**

Split the 200-line function into:
```python
def _build_gpu_metrics(snapshot):     # ~30 lines
def _build_storage_metrics(snapshot): # ~30 lines
def _build_network_metrics(snapshot): # ~25 lines
def _build_process_details(snapshot): # ~25 lines
def _fetch_rig_metrics(uuid, rig):    # orchestrator, ~80 lines
```

**2.5 Polling div → `_polling_div.html` partial**

```django
{% include "partials/_polling_div.html" with id="rig-table-container" url="..." trigger="every 30s" content_template="dashboard/_rig_table.html" %}
```

Removes 3 hand-rolled `<div hx-get=… hx-trigger=…>` blocks.

**2.6 Filter form → `_filter_form.html` partial**

The 3 filter selects in `rig_list.html` collapse into one partial that takes a list of filter definitions. Saves ~25 lines and clarifies intent.

### Phase 3: Future-ready (optional, behind feature flag)

- **HTMX 2 extensions** — already on 2.0.4. Could use `hx-on::after-request` instead of global `htmx:afterSwap` listener (cleaner scoping).
- **`hx-boost`** on the nav links to make the whole app SPA-like without writing JS.
- **WebSocket live updates** instead of 30s polling (large change — separate doc).
- **DaisyUI migration** — out of scope for this doc (already in a separate doc/branch).

---

## 3. Risk & rollback

| Phase | Risk | Mitigation |
|---|---|---|
| 0.1 (CSS class extraction) | Tailwind CDN doesn't process `@apply`. Must either use Tailwind CLI to compile, or write raw CSS. | Build step OR raw CSS (`background-color: #1f2937; border: 1px solid #374151; ...`). Raw CSS is fine for ~15 classes. |
| 0.2 (partials) | `{% include %}` with `with` syntax can have subtle variable scoping issues. | Use only documented features; test each migrated template. |
| 0.3 (color_tier helper) | New filter chain length may slightly slow rendering on big tables. | Negligible (1 call vs ~3 comparisons per cell). |
| 0.4 (JS extraction) | Load order matters; chart loaders depend on Chart.js global. | Use `defer` on all `<script>` tags, single entry point. |
| 1.1–1.7 (visual changes) | Subjective. User may not like it. | Each sub-phase is a separate commit, easy to revert. |
| 2.x (function refactor) | Refactor bugs (logic accidentally changed). | All Phase 2 work ships with unit test coverage on the refactored functions. |

**Rollback plan:** Every phase is a separate commit on a single branch. If a phase is rejected, `git revert <phase-commit>` or `git reset --hard <last-good-commit>`.

---

## 4. Estimated scope

| Phase | Files touched | New files | Net LOC delta | Visible UX change |
|---|---|---|---|---|
| 0.1 | ~10 templates | 1 (`static/css/app.css`) | ~-300 | None |
| 0.2 | ~6 templates | 3 partials | ~-80 | None |
| 0.3 | ~8 templates | +1 helper | ~-200 | None |
| 0.4 | `rig_detail.html`, `base.html` | 6 JS files | ~-900 from template, +500 in JS | None |
| 1.x | ~4 templates | few partials | ~+50 | Readability improvements |
| 2.x | `views.py`, `gpu_filters.py`, JS | — | ~-150 | None |
| **Total** | | | **~-1,080 LOC** | **Modest visual improvements** |

---

## 5. Suggested order

I recommend landing in this order:

1. **Phase 0.1** (CSS) — pure refactor, no risk, immediate readability win in code
2. **Phase 0.4** (JS extraction) — biggest LOC win, isolates future JS work
3. **Phase 0.3** (color thresholds) — fixes the inconsistencies found in §1.5
4. **Phase 2.x** (code simplification) — cleanups that compound with 0.1–0.3
5. **Phase 1.x** (visual readability) — last, because subjective

Each phase lands on the same `plan/layout-optimization` branch as separate commits. Branch stays open until user merges.

---

## 6. Awaiting user decision

Please confirm:
- **Scope**: all of Phase 0 + Phase 2, then visual (Phase 1)? OR just Phase 0?
- **CSS strategy**: raw CSS in `static/css/app.css` (no build step) OR introduce Tailwind CLI to compile `@apply`?
- **Visual direction**: any preferences on §1.1–1.7? (column grouping, collapsible rows, sticky chart nav, etc.)
- **Branch policy**: keep plan on `plan/layout-optimization` and ship implementation on separate `chg/` or `feat/` branches off `main`?
