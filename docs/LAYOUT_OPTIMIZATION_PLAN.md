# Layout & Code Optimization Plan

**Branch:** `feat/layout-optimization`
**Status:** Phases 0.1, 0.2, 0.3, 0.4, 0.5 done. 18 commits ahead of main, all pushed.
**Last updated:** after the simplify-cleanup commit (`f719e38`) + the chart-loaders refactor (`418fdd1`).

## Guiding principle (from the user)

> **Use what is already available in Tailwind, do not recreate something that already exists. If existing Tailwind functionality is similar enough that can be reused then reuse it. We aim to simplify code while retaining functionalities.**

In practice this means:
- **Don't** create custom CSS classes that just reimplement Tailwind utilities (`grm-text-red` → use `text-red-400`).
- **Don't** create Python filter wrappers that just reimplement what Tailwind classes already do.
- **Do** create a partial or helper when a SPECIFIC UI pattern repeats (a card, a form input, a chart card) — those aren't generic utilities, they're a domain component.
- **Do** create one function/class for a domain concept — DRY at the domain level, not the utility level.

This principle ruled Phase 0.5 (deleted 28 redundant CSS rules), commit `f719e38` (deleted dead `tier` filter + `grm-select` + redundant W005 check + stale docstring examples), and commit `418fdd1` (extracted `legendOptions` + `buildChartUrl` to Base in chart-loaders.js).

---

## 1. Findings from analysis (final, post-Phase 0.5)

### 1.1 Inventory — current state (vs. plan-time)

| Area | Files | LOC then | LOC now | Δ |
|---|---|---|---|---|
| Templates (HTML) | 27 | ~3,980 | ~3,800 | -180 |
| `rig_detail.html` | 1 | 1,441 | 358 | **-1,083** |
| `_metrics_cards.html` | 1 | 942 | 979 | +37 (gains from new bar) |
| Views (Python) | 4 | ~1,229 | ~778 + 524 + 1,130 + 621 = ~3,053 | +1,824 (mostly tests) |
| Templatetags (Python) | 1 | 436 | 621 | +185 (helpers + tests) |
| Static JS (new) | 6 files | 0 | 1,419 | +1,419 |
| CSS | 1 file | 360 | 358 | -2 |

The big wins are: rig_detail 1,083 lines saved; 28 CSS rules deleted.

### 1.2 Tailwind duplication (fully resolved)

Phase 0.1 wrapped the duplication in custom classes; Phase 0.5 deleted them entirely. Templates now use Tailwind utilities directly:
- 26 uses of `grm-input` (form input — domain component, kept)
- 249 uses of `text-gray-300/400/500` (Tailwind utilities, direct)
- All `bg-X-400` / `text-X-400` come from the tier filter output
- 0 uses of any deleted class prefix (W005 system check confirms)

### 1.3 HTMX duplication (low value to fix)

- **Polling div** — 3 instances in `rig_detail.html` (15s) and `rig_list.html` (30s). Each is 5 lines. A `_polling_div.html` partial would save ~10 lines but introduce a new abstraction. **Verdict: skip**, 5 lines × 3 sites is not enough.
- **Filter form** — 3 inputs in `rig_list.html` (search, status, tag). Each is 8-10 lines with repeated `hx-include` and `hx-get`. A partial would help. **Verdict: low priority**, the inline form is readable.

### 1.4 JavaScript duplication (fully resolved)

Phase 0.4 extracted the ~900 lines of inline JS into 6 files. Commit `418fdd1` further simplified `chart-loaders.js` by:
- Extracting `Base.legendOptions()` — 6 inline legend objects → 1-line calls
- Extracting `Base.buildChartUrl()` — 7 inline URL constructions → 1-line calls
- Removing `bailNoData` wrapper (3 sites use `Base.noDataMessage` directly)

`chart-loaders.js`: 632 → 585 lines. No further DRY opportunities in the JS layer.

### 1.5 Color threshold logic (fully resolved)

Phase 0.5 redesigned the tier system:
- `tier_text` / `tier_fill` return Tailwind classes (e.g. `text-red-400` / `bg-red-400`)
- `color_tier_thresholds` is a `simple_tag` that returns the spec list
- Templates compose them as `class="{{ x|tier_text:spec }}"` — no custom CSS

10 specs in `DEFAULT_THRESHOLDS` are the single source of truth. The "tier" filter (returns bare color name) was deleted in `f719e38` as dead code.

### 1.6 Maintainability issues (final status)

- ✅ `rig_detail.html` mixes 3 concerns — **FIXED** in Phase 0.4
- ⚠️ `_metrics_cards.html` is 979 lines — 8 inline sections; partials would help but each section is unique. **Verdict: skip**, refactoring 8 unique sections into partials adds more indirection than it removes.
- ✅ `gpu_filters.py` had 3 near-identical `gpu_*_cell_json` simple_tags — **FIXED** via shared `_render_tier_cell` helper
- ⏳ `dashboard/views.py` `_fetch_rig_metrics` is ~485 lines (41 calls to `_json_get`) — **Phase 2.4 candidate**
- ✅ `base.html` had growing inline JS — **FIXED** via `app-base.js`

---

## 2. Plan — phased rollout (final)

### Phase 0: Foundation (all done)

| Sub-phase | What | Status | Commit |
|---|---|---|---|
| 0.1 | Extract shared CSS classes | ✅ Done | `3253372` |
| 0.2 | Create reusable template partials (`_chart_card.html`, `_form_input.html`) | ✅ Done | `83c11b9` |
| 0.3 | Unify 5-tier color thresholds into a `color_tier_thresholds` filter | ⚠️ Reworked in 0.5 | `83c11b9` |
| 0.4 | Extract JavaScript from `rig_detail.html` into 6 files | ✅ Done | `94ba363` |
| 0.5 | **Delete custom color CSS, use Tailwind directly** | ✅ Done | `dc290de` |
| Fix | Multi-line `{# #}` comments split | ✅ Done | `67ad43c` |
| Fix | `_paths.py` shared test helper | ✅ Done | `7252b23` |
| Fix | Bump colors to -400 series for visibility | ✅ Done | `8a26ae4` |
| Fix | Add missing orange/muted fill classes | ✅ Done | `e849f49` |
| Fix | Double-prefix tier filter bug (`text-text-X-400-400`) | ✅ Done | `a7d39a1` |
| Fix | GPU util spec thresholds (80% should be yellow, not gray) | ✅ Done | `54b3643` |
| Simplify | Delete dead code: `tier` filter, `grm-select`, redundant W005 | ✅ Done | `f719e38` |
| Simplify | `chart-loaders.js` — extract `legendOptions` + `buildChartUrl` | ✅ Done | `418fdd1` |

### Phase 1: Readability (visual, 1.1-1.3 done; 1.4-1.7 skipped per user)

| Sub-phase | What | Status | Notes |
|---|---|---|---|
| 1.1 | Fleet table column grouping via `<colgroup>` | ✅ Done | `ba68f6c` |
| 1.2 | Multi-GPU cell separators (`·` → space) | ✅ Done | `ba68f6c` |
| 1.3 | "System health" summary bar | ✅ Done | `ba68f6c` |
| 1.4 | Sticky sub-nav inside Charts tab | ⏭️ Skipped | Out of scope per user feedback |
| 1.5 | Collapsible row expansion in fleet table | ⏭️ Skipped | Low value |
| 1.6 | Merged "Events" tab (Containers + Errors) | ⏭️ Skipped | Low value |
| 1.7 | 2-column Live Metrics on `lg:` screens | ⏭️ Skipped | Current stacking is fine |

### Phase 2: Code simplification (mostly done)

| Sub-phase | What | Status | Notes |
|---|---|---|---|
| 2.1 | Collapse `gpu_*_cell_json` into one helper | ✅ Done | `83c11b9` |
| 2.2 | Collapse `loadChart*` JS into one function | ✅ Done | `94ba363` + `418fdd1` |
| 2.3 | Status badge consolidation | ⏭️ Dropped | Already done by `grm-badge-*` in 0.1 |
| 2.4 | `_fetch_rig_metrics` split into per-device builders | ⏳ **TODO** | Real DRY win — 485-line function with 41 `_json_get` calls |
| 2.5 | Polling div → `_polling_div.html` partial | ⏭️ Skipped | Low value (3 sites × 5 lines) |
| 2.6 | Filter form → `_filter_form.html` partial | ⏭️ Skipped | Low value (3 inputs × 10 lines) |

---

## 3. Remaining work

### Phase 2.4: Split `_fetch_rig_metrics` (the only real Phase 2 win)

**Current:** `dashboard/views.py` line 175+. The function is ~485 lines (40% of the file) and builds 5 different dict structures (gpu_metrics, storage_metrics, network_metrics, process_details, docker_metrics) with the same `_json_get(snapshot.X_json, i)` pattern repeated 41 times.

**Plan:** Extract one builder per device type:
```python
def _build_gpu_metrics(snapshot):     # ~30 lines (gpu_uuids, gpu_models, gpu_temps, etc.)
def _build_storage_metrics(snapshot): # ~30 lines (storage_devices, fstypes, mountpoints, etc.)
def _build_network_metrics(snapshot): # ~25 lines (network_interfaces, ipv4s, etc.)
def _build_process_details(snapshot): # ~25 lines (process_top, process_top_rss, etc.)
def _build_docker_metrics(uuid, snapshot): # ~20 lines (already has its own query)
def _fetch_rig_metrics(uuid, rig=None):
    # orchestrator: ~30 lines, calls the builders
```

**Expected benefit:** Better testability (each builder can be unit-tested independently), better readability (the orchestrator shows the high-level shape).

**Risk:** 41 calls of `_json_get` use a complex snapshot object. If the builders' signatures are wrong, the views will silently fail. Need to keep behavior identical — run all tests after refactor.

**Effort:** ~2-3 hours.

### Stale documentation in `gpu_filters.py`

While auditing for Phase 2.4, I found that the docstring example for `color_tier_thresholds` still references the deleted `tier` filter and the deleted `grm-text-` class prefix. This is a minor cleanup:

```python
# Before (stale):
"""
Usage:
    {% color_tier_thresholds "cpu_temp" as cpu_temp_thresholds %}
    <span class="grm-text-{{ val|tier:cpu_temp_thresholds }}">
"""
# After (correct):
"""
Usage:
    {% color_tier_thresholds "cpu_temp" as cpu_temp_thresholds %}
    <span class="{{ val|tier_text:cpu_temp_thresholds }}">
"""
```

This is 5 minutes of work. I can roll it into the Phase 2.4 commit.

### Why not the rest of the plan

- **Polling div / filter form partials (2.5, 2.6):** too few sites (3 each). The simplify principle says don't add abstractions without justification.
- **Visual follow-ups (1.4-1.7):** the user has already pushed back once on visual noise (commit `ba68f6c` shrunk the system health bar). Following the same logic, don't add more visual elements without being asked.
- **More chart-loaders refactor:** none found in `418fdd1`. The 7 loaders are now thin wrappers.

---

## 4. The simplify-and-reuse principle — applied to this plan

> **Use what is already available in Tailwind, do not recreate something that already exists.**

Concretely:
- ✅ Do create **domain components** (`grm-input`, `grm-card`, `grm-btn-primary`, `grm-badge-online`, `grm-progress`, `_chart_card.html`) — they compose multiple Tailwind utilities
- ✅ Do create **DRY helpers** for domain logic (`_render_tier_cell` for multi-GPU cell rendering, `Base.buildChartUrl` for API URL construction, `Base.legendOptions` for chart legend config, `color_tier_thresholds` for the threshold spec)
- ❌ Do NOT create **CSS reimplementations of Tailwind utilities** (Phase 0.5 deleted 28 such rules)
- ❌ Do NOT create **Python wrappers** that just wrap Tailwind classes
- ❌ Do NOT add **visual features** the user didn't ask for

This rule is what led to dropping 2.3, 2.5, 2.6 and all of 1.4-1.7.

---

## 5. Risk & rollback

| Phase | Risk | Mitigation |
|---|---|---|
| Phase 0.5 (Tailwind direct) | None — just deletes redundant CSS | Trivial to revert if needed |
| Phase 2.4 (`_fetch_rig_metrics` split) | Refactor bugs (logic accidentally changed) | Run all tests after refactor; keep behavior identical |
| `color_tier_thresholds` docstring | None — just fixes a stale example | Trivial |

**Rollback plan:** Every commit is self-contained on `feat/layout-optimization`. `git revert <commit>` or `git reset --hard <last-good-commit>` to back out.

---

## 6. Branch policy (current)

- **All work** is on the **same branch**: `feat/layout-optimization`. Per the user's request: "We want to deliver final result in single layout-optimization branch."
- 18 commits ahead of `main`, all pushed.
- **Do not create new branches** for this work.
- **Do not edit `/opt/`** — let the user run `collectstatic` after merging.

---

## 7. Status check (what's done vs what's left)

### Done in this session (18 commits)

| Done | Commit |
|---|---|
| Phase 0.1: extract shared CSS classes | `3253372` |
| Deploy fix: always run collectstatic + Django system check | `eef4006` |
| Phase 0.2: `_chart_card.html` + `_form_input.html` partials | `83c11b9` |
| Phase 0.3: unified color threshold system | `83c11b9` |
| Phase 0.4: extract JS from rig_detail.html (6 files) | `94ba363` |
| Phase 1.1: column grouping via `<colgroup>` | `ba68f6c` |
| Phase 1.2: multi-GPU cell separators (reverted to space) | `ba68f6c` |
| Phase 1.3: system health bar at top of Live Metrics | `ba68f6c` |
| Phase 2.1: collapse `gpu_*_cell_json` | `83c11b9` |
| Phase 2.2: collapse `loadChart*` JS | `94ba363` |
| Multi-line comment fix | `67ad43c` |
| Test directory reorg + `tests/_paths.py` | `b00cf8f` + `7252b23` |
| Bump colors to -400 series for visibility | `8a26ae4` |
| Add missing orange/muted fill classes | `e849f49` |
| **Phase 0.5: delete custom color CSS, use Tailwind directly** | `dc290de` |
| Plan doc rewrite for simplify+reuse principle | `c2ffa5e` |
| Simplify cleanup: drop dead `tier` filter, `grm-select`, redundant W005 | `f719e38` |
| Chart-loaders refactor: extract `legendOptions` + `buildChartUrl` | `418fdd1` |

### Left to do

| Work | Why |
|---|---|
| **Phase 2.4**: split `_fetch_rig_metrics` into per-device builders | 485-line god function, 41 `_json_get` calls — real DRY win, 2-3h work |
| Stale docstring fix in `color_tier_thresholds` | 5 min cleanup, can be rolled into 2.4 commit |
| Phase 1.4-1.7 visual follow-ups | User explicitly trimmed visual noise in `ba68f6c`; same principle says don't add more |

---

## 8. Awaiting decision

The simplify-and-reuse directive argues for **stopping** unless the user has a specific need. The remaining items are:

1. **Phase 2.4** (`_fetch_rig_metrics` split) — only if testability benefit justifies the work; includes the docstring fix
2. **Visual follow-ups (1.4-1.7)** — only when the user asks for them

**Recommendation:** the work is in a good state. The user can merge `feat/layout-optimization` to `main` whenever they're ready. The only remaining work is Phase 2.4, which is a real DRY win but is *not necessary* for the user's goals. If you want me to do Phase 2.4 anyway, I can — it'll save ~200 lines of the 485-line function and make each device's logic independently testable. Otherwise, the branch is ready to merge.
