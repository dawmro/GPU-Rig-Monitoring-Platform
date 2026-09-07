# Layout & Code Optimization Plan

**Branch:** `feat/layout-optimization` (replaces earlier `plan/layout-optimization`)
**Status:** Phases 0.1, 0.2, 0.3, 0.4, 0.5 done. Phase 2.4 + visual follow-ups remaining.
**Last updated:** after Phase 0.5 — when the user directed us to **reuse Tailwind utilities instead of recreating them as custom classes**.

## Guiding principle (from the user)

> **Use what is already available in Tailwind, do not recreate something that already exists. If existing Tailwind functionality is similar enough that can be reused then reuse it. We aim to simplify code while retaining functionalities.**

In practice this means:
- **Don't** create custom CSS classes that just reimplement Tailwind utilities (`grm-text-red` → use `text-red-400`).
- **Don't** create Python filter wrappers that just reimplement what Tailwind/Tailwind classes already do.
- **Do** create a partial or helper when a SPECIFIC UI pattern repeats (a card, a form input, a chart card) — those aren't generic utilities, they're a domain component.
- **Do** create one function/class for a domain concept (e.g. `_status_badge` for online/stale/offline states) — DRY at the domain level, not the utility level.

This principle invalidates some of the original plan below (Phase 0.3 and parts of Phase 2) and simplifies the rest.

---

## 1. Findings from analysis (original)

### 1.1 Inventory (snapshot at plan time)

| Area | Files | Total LOC |
|---|---|---|
| Templates (HTML) | 27 | ~3,980 |
| Views (Python) | 4 modules | ~1,229 |
| Templatetags (Python) | 1 (`gpu_filters.py`) | 436 |
| Models (Python) | 4 modules | — |

Largest files at plan time:
- `templates/dashboard/rig_detail.html` — 1,441 lines (had ~900 lines inline JS)
- `templates/dashboard/_metrics_cards.html` — 942 lines
- `templates/dashboard/views.py` — 778 lines
- `templates/dashboard/templatetags/gpu_filters.py` — 436 lines

### 1.2 Tailwind duplication (had to be addressed)

The same Tailwind recipe was repeated dozens of times. **The fix (Phase 0.1) was to wrap them in named custom classes** (`.grm-card`, `.grm-btn-primary`, etc.) — but **after the user's directive in Phase 0.5**, we **deleted those custom classes** and now use Tailwind utilities directly. The duplication is gone without us needing to maintain a parallel CSS file.

### 1.3 HTMX duplication patterns (still relevant)

- **Polling div** — same `<div hx-get=… hx-trigger="every 30s" hx-target=… hx-swap="innerHTML" hx-indicator=…>` shape appears 3 times. **Action:** extract to `_polling_div.html` partial.
- **Search input with debounced keyup + filter selects** — 3 controls each re-declare `hx-include="[name='search'], [name='status'], [name='tag']"` in different subsets. **Action:** extract to `_filter_form.html` partial (low priority — the current code is fine and the partial would just hide complexity).
- **Form with CSRF + hx-post + swap into row** — 5+ instances. **Action:** keep as-is, the inline form pattern is more readable than a partial here.

### 1.4 JavaScript duplication (Phase 0.4 already addressed)

The ~900 lines of inline JS in `rig_detail.html` were extracted to 6 files under `static/js/`:
- `app-base.js` (clocks, mobile menu, email toggle)
- `chart-base.js` (shared Chart.js options)
- `chart-colors.js` (4 palettes)
- `chart-loaders.js` (7 chart loader functions)
- `chart-runtime.js` (chart state, staggered loading)
- `rig-detail.js` (tab switching, modal, rename)

`rig_detail.html` shrank from 1,441 → 358 lines (-75%).

### 1.5 Color threshold logic (Phase 0.5 already addressed)

**Original problem:** 14 inline `{% if %}…{% elif %}…{% else %}…` chains across 3 languages, with 5+ inconsistencies between them (e.g. CPU temp 85/70 vs 80/70, GPU temp 80/75/70/65 vs 80/70).

**Resolution:** Single Python helper `tier_text` / `tier_fill` that returns bare color names (e.g. `'red'`). Templates compose them as `text-X-400` / `bg-X-400` using **Tailwind directly** (no custom CSS). 10 specs in `DEFAULT_THRESHOLDS` are the single source of truth. The CSS file no longer contains color classes (they were duplicates of Tailwind).

### 1.6 Maintainability issues (status after Phase 0.5)

- ~~`rig_detail.html` mixes 3 concerns~~ — **FIXED** in Phase 0.4 (358 lines, all JS extracted)
- ~~`_metrics_cards.html` is 942 lines because it inlines 8 large sections~~ — partially addressed; 942 → 838 lines after Phase 0.5 refactor
- ~~`gpu_filters.py` has 3 near-identical `gpu_*_cell_json` simple_tags~~ — **FIXED** in Phase 0.5 (all 3 use shared `_render_tier_cell` helper)
- `dashboard/views.py` `_fetch_rig_metrics` is ~200 lines and builds 5 different dict structures (gpu/storage/network/processes/docker) with the same `_json_get(snapshot.X, i)` pattern repeated dozens of times. **Still relevant for Phase 2.4.**
- ~~`base.html` has growing inline JS~~ — **FIXED** in Phase 0.4 (extracted to `static/js/app-base.js`)

---

## 2. Plan — phased rollout (revised after Phase 0.5)

### Phase 0: Foundation (no behavior change) — mostly done

> Pure refactor. Zero visible difference to the user. Each phase lands as a separate commit on the same branch.

| Sub-phase | What | Status | Notes |
|---|---|---|---|
| 0.1 | Move shared CSS classes to `static/css/app.css` | ✅ Done | Added `.grm-card`, `.grm-btn-primary`, etc. |
| 0.2 | Create 3 reusable template partials (`_card.html`, `_form_input.html`, `_chart_card.html`) | ✅ Done | `_card.html` was skipped (Django doesn't support content blocks) |
| 0.3 | Unify 5-tier color thresholds into a single `color_tier` filter | ⚠️ Obsolete | See 2.1 below — the user's directive made us drop this |
| 0.4 | Extract JavaScript from `rig_detail.html` into 6 files | ✅ Done | `rig_detail.html`: 1441 → 358 lines |
| 0.5 | **Delete custom color CSS, use Tailwind utilities directly** | ✅ Done | Removed 28 redundant `grm-text-*` / `grm-progress-fill-*` rules; templates use `text-X-400` / `bg-X-400` |

### 2.1 What 0.3 became (revised by user directive)

The original plan was to create a Python `color_tier` filter that returned CSS class names like `grm-text-red`. **The user pointed out this is duplicating what Tailwind already does.** So the filter was redesigned to return **bare color names** (e.g. `'red'`), and templates compose them as `text-X-400` / `bg-X-400` using **Tailwind directly** — no custom CSS class.

The 5-tier color logic is still centralized in Python (10 specs in `DEFAULT_THRESHOLDS`), but the *output* is just a color name; the *rendering* is Tailwind. The simplification is good because:
- We don't maintain a parallel CSS file of color rules
- Templates use the same Tailwind utilities they would use for any color
- If Tailwind changes its palette, we update one place (Tailwind) not two

### Phase 1: Readability (visual, no behavior change)

> Each sub-phase is a separate commit. Mostly subjective.

| Sub-phase | What | Status | Notes |
|---|---|---|---|
| 1.1 | Fleet table — column grouping via `<colgroup>` | ✅ Done | 5 group tints (identity/status/gpu/system/meta) |
| 1.2 | Multi-GPU cell separators (`·` → space) | ✅ Done | `GRM_MULTI_VALUE_SEPARATOR = ' '` |
| 1.3 | "System health" summary bar at top of Live Metrics | ✅ Done | Trimmed to just hardware info (CPUs/GPUs/Disks) after user feedback |
| 1.4 | Sticky sub-nav inside Charts tab | ⏳ Not done | Out of scope; charts are well-organized via the card title text |
| 1.5 | Collapsible row expansion in fleet table | ⏳ Not done | Low priority; the multi-value cell already shows the full list |
| 1.6 | Merged "Events" tab (Containers + Errors) | ⏳ Not done | Low priority; the two tabs work fine |
| 1.7 | 2-column Live Metrics on `lg:` screens | ⏳ Not done | Cards stack today; the 8-card stack is OK on tall screens |

### Phase 2: Code simplification (no behavior change)

> Most of Phase 2 was obviated by the simplify-and-reuse directive. Only 2.4 and 2.6 are still relevant.

| Sub-phase | What | Status | Notes |
|---|---|---|---|
| 2.1 | Collapse `gpu_*_cell_json` into one helper | ✅ Done | All 3 use shared `_render_tier_cell` in Phase 0.5 |
| 2.2 | Collapse `loadChart*` JS into one function | ✅ Done | All 7 chart loaders use shared `chart-base` (Phase 0.4) |
| 2.3 | Status badge consolidation | ❌ Dropped | Already done by the unified `grm-badge-*` classes in Phase 0.1 |
| 2.4 | `_fetch_rig_metrics` — extract per-device builders | ⏳ **TODO** | Real DRY win — 200-line god function |
| 2.5 | Polling div → `_polling_div.html` partial | ⏳ Not done | Low value; 3 instances is manageable |
| 2.6 | Filter form → `_filter_form.html` partial | ⏳ Not done | Low value; same reason |

---

## 3. Remaining work (after Phase 0.5)

### Phase 2.4: Split `_fetch_rig_metrics` (the only real Phase 2 win)

**Current:** `dashboard/views.py` line 177+. The function is ~200 lines and builds 5 different dict structures (gpu_metrics, storage_metrics, network_metrics, process_details, docker_metrics) with the same `_json_get(snapshot.X, i)` pattern repeated dozens of times.

**Plan:** Extract one builder per device type:
```python
def _build_gpu_metrics(snapshot):     # ~30 lines
def _build_storage_metrics(snapshot): # ~30 lines
def _build_network_metrics(snapshot): # ~25 lines
def _build_process_details(snapshot): # ~25 lines
def _fetch_rig_metrics(uuid, rig=None):
    # orchestrator: ~50 lines, calls the builders
```

**Expected benefit:** Better testability (each builder can be unit-tested), better readability (the orchestrator shows the high-level shape).

**When to skip:** if `_fetch_rig_metrics` is rarely modified and the test coverage is already strong, leave it alone. The simplify-and-reuse directive argues for NOT touching code that works.

### Visual follow-ups (Phase 1.4, 1.5, 1.6, 1.7)

The user said the user "didn't like the [system health bar showing max 0%, errors 0, live]" — they wanted only useful info. By the same logic, do NOT add more visual clutter that the user might not like. **Only add visual elements when the user asks for them.**

---

## 4. The simplify-and-reuse principle — applied to this plan

> **Use what is already available in Tailwind, do not recreate something that already exists.**

Concretely:
- ✅ Do create **domain components** (a status badge, a chart card, a polling div) — they encapsulate repeated UI patterns
- ✅ Do create **DRY helpers** for domain logic (the tier system, the status badge)
- ❌ Do NOT create **CSS reimplementations of Tailwind utilities**
- ❌ Do NOT create **Python wrappers** for what Tailwind/Tailwind classes already do
- ❌ Do NOT add visual features the user didn't ask for

This is why Phase 0.5 deleted 28 CSS rules and why 2.1, 2.2, 2.3, 2.5, 2.6 were dropped — they were reimplementations or low-value wrappers.

The remaining work (Phase 2.4 + visual follow-ups) is justified only if the work is **necessary** for the user's goals, not because the original plan said so.

---

## 5. Risk & rollback

| Phase | Risk | Mitigation |
|---|---|---|
| 0.1 (CSS class extraction) | Done. | Done. |
| 0.2 (partials) | `{% include %}` with `with` syntax can have subtle variable scoping issues | Use only documented features; test each migrated template |
| 0.3 (color_tier helper) | Replaced by direct Tailwind usage (Phase 0.5) | N/A |
| 0.4 (JS extraction) | Load order matters; chart loaders depend on Chart.js global | Use `defer` on all `<script>` tags, single entry point |
| 0.5 (Tailwind direct) | None — just deletes redundant CSS | Trivial to revert if needed |
| 2.4 (`_fetch_rig_metrics` split) | Refactor bugs (logic accidentally changed) | All work ships with unit test coverage |

**Rollback plan:** Every phase is a separate commit. If a phase is rejected, `git revert <phase-commit>` or `git reset --hard <last-good-commit>`.

---

## 6. Branch policy (current)

- **All phases** (`0.1`, `0.4`, `0.5`, `1.1`, `1.2`, `1.3`, and the deploy-fix commit) live on the **same branch**: `feat/layout-optimization`. Per the user's request: "We want to deliver final result in single layout-optimization branch."
- The branch is 12 commits ahead of `main`, all pushed.
- **Do not create new branches** for this work; stay on `feat/layout-optimization`.
- **Do not edit `/opt/`** — let the user run `collectstatic` after merging.

---

## 7. Status check (what's done vs what's left)

| Done in this session | Commit |
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
| **Plan doc rewrite for simplify+reuse principle** | (this commit) |
| Codebase simplification cleanup (docstring trim, `_paths` reuse, updated tests) | (this commit) |

| Left to do | When |
|---|---|
| Phase 2.4: split `_fetch_rig_metrics` into per-device builders | Only if testability benefit justifies the work |
| Phase 1.4, 1.5, 1.6, 1.7 (visual follow-ups) | Only when the user asks for them |

---

## 8. Awaiting decision

The simplify-and-reuse directive argues for **stopping** unless the user has a specific need. The remaining items are:

1. **Phase 2.4** (`_fetch_rig_metrics` split) — only if testability benefit justifies the work
2. **Visual follow-ups (1.4-1.7)** — only when the user asks for them

**Recommendation:** the work is in a good state. The user can merge `feat/layout-optimization` to `main` whenever they're ready. Any further changes should be driven by a concrete user need, not by completing the original plan.
