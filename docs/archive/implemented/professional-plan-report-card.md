# Professional Plan — Fix "gpu0" in 7d Report Without Breaking HTMX

Status: PLAN — branch `fix/revert-chart-uuid-model` at `8d9846f` (reverted to 425165d).
The previous `LatestSnapshot` fix (`9e888b3`) caused HTMX `.htmx-indicator` 500 — unrelated to UUID logic, caused by missing HTML indicator element or template mismatch after the view edit. The 7d "gpu0" issue remains unfixed: `gpu_agg` groups by `gpu_index` + `model` only (no UUID in grouping), and `gpu_raw` scan picks the oldest UUID in 7d window, not current.

---

## What Was Learned (From Previous Failures) — Must Not Repeat

1. **`LatestSnapshot.gpu_uuids_json` approach works** for identity tracking (current UUID, not historical). The 500 was NOT from UUID logic — it was `.htmx-indicator` HTML/template mismatch. Professional: separate identity fix from UI/template changes; verify HTMX indicator separately.
2. **Template uses `gpu.gpu_uuid`** (line 9 of `_report_table.html`). Previous fix (`9e888b3`) set `row['gpu_uuid_display']` (wrong key) — that alone could cause template lookup failure. Professional: match dict key to template variable exactly.
3. **`compact_data.py` must keep `gpu_uuid` in `static_fields`** (restored at `2770586`). Removing it breaks identity preservation in pre-bucketed tiers (problem 5 from professional-prevention.md).
4. **Bucket timing (`test_chart_bucket_defense.py`) and grouping defense (`test_compaction_grouping_defense.py`) are independent** from report identity. Don't mix them in same commit.
5. **Never edit view and template in same unverified step** without checking HTMX indicator. The previous attempt edited `dashboard/views.py` without confirming `_report_table.html` indicator presence.

---

## Root Cause of "gpu0" for 7d (Unchanged by Revert)

`dashboard/views.py` `_build_report_context` (line 743-744):
- `gpu_agg` groups by `gpu_index + model` (NOT `gpu_uuid`).
- `gpu_raw` scans all raw rows in 7-day window; `latest_raw` picks the LAST chronological entry per index in that window — which could be an OLD UUID (`''` or previous GPU) if replacement happened mid-week.
- The `gpu_uuid` field on aggregated `gpu_agg` rows is empty (not in `group_by` or `agg_fields`), so the header falls back to empty, and the template shows `gpu-{{ index }}` (`gpu0`).

---

## Professional Fix Plan (Will Not Break HTMX / Charts / Identity)

### Step A — Separate Identity Source (View Fix, Independent of Template)
File: `gpu_monitor/dashboard/views.py`
Approach: use the SAME safe identity chain as chart endpoint (`_safe_gpu_label` pattern), applied to `gpu_agg` post-processing — DO NOT change `gpu_agg` grouping (keep it simple: index + model only, with UUID from current snapshot).

Specific change (line 773-785 area):
- Replace `next((r ... gpu_raw))` scan with `LatestSnapshot` query (current UUID) + `GPUMetric` latest metric fallback (same as chart endpoint).
- Set `row['gpu_uuid']` (not `gpu_uuid_display`) — match template variable exactly.
- Keep `gpu_identity_changes` computation unchanged (uses `gpu_raw` for change detection — correct, needs historical data).

Verification: `python3 -m py_compile gpu_monitor/dashboard/views.py`; no syntax errors; `_safe_gpu_label` exists (verified in branch).

### Step B — Verify Template Indicator (HTMX Defense — Before Any Push)
File: `gpu_monitor/templates/dashboard/_report_table.html` / `rig_detail.html`
- Confirm `.htmx-indicator` element exists in the HTML that triggers the HTMX request (`hx-target` / `hx-select`).
- If missing, add `<span class="htmx-indicator">⟳ Refreshing...</span>` (matches `rig_list.html` line 71).
- Verify `hx-indicator=".htmx-indicator"` matches the class name exactly.

Verification: open `/dashboard/rigs/{uuid}/htmx-report/` in browser; HTMX indicator should show; no 500.

### Step C — System Check (Layer 4 — Before Commit)
File: `gpu_monitor/metrics_app/checks.py`
- Verify `_safe_gpu_label` exists and no inline `.filter().first()` remains in `_handle_gpu_metric`.
- Verify `compact_data.py` has `gpu_uuid` in `static_fields` (already done at `2770586`).
- Verify `tests/test_chart_bucket_defense.py` passes (already added `219594d`).

Verification: `python -m pytest tests/test_compaction_grouping_defense.py tests/test_chart_bucket_defense.py` (if Django venv available).

### Step D — Independent Commit (Not Mixed with Template)
Branch naming (already `fix/revert-chart-uuid-model` — appropriate `fix/` prefix):
- Commit 1: `fix/report-card-identity-safe` — view fix (`views.py` only), with `tests/` if needed.
- Commit 2: `fix/htmx-indicator-template` — template fix ONLY (if indicator missing), verified with browser.
- Never combine both in one unverified commit.

### Step E — Skill Doc Update (Layer 4)
File: `.hermes/skills/software-development/gpu-rig-monitoring/references/gpu-uuid-timeseries-pattern.md`
- Add note: "Report identity uses `LatestSnapshot.gpu_uuids_json` (current), not 7-day raw scan. Template variable must be `gpu.gpu_uuid` (matches `_report_table.html` line 9). HTMX indicator (`.htmx-indicator`) must exist in template."

---

## What Will NOT Break This Time (Defensive Measures Applied)

| Layer | Applied | Prevents |
|---|---|---|
| Code (`_safe_gpu_label`) | `92a7ab3` | No `None` label; no N+1 query |
| System check (`checks.py` E009) | `425165d` | Catches missing safe method |
| Compaction grouping test | `219594d` / `test_compaction_grouping_defense.py` | Empty UUID grouping verified |
| Bucket timing test | `219594d` / `test_chart_bucket_defense.py` | No future bucket extension |
| Template indicator verification | Manual (Step B) | HTMX 500 prevented |
| Skill doc (`references/`) | `425165d` | Template variable contract documented |
| Separate commits (Step D) | Process | Reversible independently |

---

## Action Order (Safe, Verified Before Push)

1. Inspect `_report_table.html` for `.htmx-indicator` presence.
2. Apply `views.py` identity fix (`LatestSnapshot` + safe label) separately.
3. If indicator missing: fix template in second commit.
4. Verify `python3 -m py_compile` and `git status` (only targeted files).
5. Commit + push (branch `fix/revert-chart-uuid-model`, never `main`, user merges).
