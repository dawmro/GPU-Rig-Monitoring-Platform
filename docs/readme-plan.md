# README Enhancement Plan — Professional, Brief, Comprehensive
Branch: `feat/readme-comprehensive` (self-contained; never `main`; user merges).
Status: **PLANNED — NOT YET IMPLEMENTED — AWAITING USER APPROVAL** (per user workflow).

---

## User Requirement (Verbatim)

> "create new branch to work on updating README.md with detailed and comprehensive list of features, charts types, reports. We want to make readme the most informative as possible, but still keeping it brief and professional."

---

## Professional Design Principles (Not Over-Engineering)

Per user's conventions (memory / `docs/professional-prevention.md` / `docs/archive-plan.md`):
- Brief = concise tables + structured sections; not verbose paragraphs.
- Professional = cited facts (line numbers, verified counts), not marketing fluff.
- Self-contained = one commit; independent revert; no other file changes (only `README.md`).
- Documented = reference `docs/archive-plan.md` (what was cleaned) and `docs/professional-plan-chart-refactor.md` (6 features).

---

## What Will Be Added (Concise Sections Only — Not Verbose Expansion)

### Section: Feature Inventory (Table Format, Brief)
Added after `## ✨ Key Features` or within it — a compact table referencing verified components:

|| Feature | Component | Verification Reference |
|---|---|---|---|
| Chart | Multi-GPU (8 loaders) | `loadChartMultiGpu()` (`chart-loaders.js:499`) | `tests/test_chart_loaders_unit.py` |
| Chart | Network Combined (dual axis) | `loadChartNetworkCombined()` (`chart-loaders.js:200`) | `fetchNetworkData()` + `buildNetworkDatasets()` composable (`93cf7f1`) |
| Chart | Job Status (bool) | `loadChart()` (`SNAPSHOT_METRICS` `has_active_job`) | `Max(Cast('has_active_job', IntegerField()))` compaction defense (`compact_data.py:209`) |
| Report | GPU Identity | Report card (`_report_table.html`) | Current UUID from `LatestSnapshot.gpu_uuids_json` (not historical scan) |
| Compaction | 3-tier retention | `compact_data.py` | `gpu_uuid` in `static_fields` (`E008`); bool cast defense (`E009`) |
| Security | 4-layer defense | `metrics_app/checks.py` | `E001`–`E010` (model + endpoint + serializer + compaction + identity + budget) |

### Section: Architecture (One-Line Reference — Not Full Repaste)
Added brief note: full architecture diagram in `docs/GPU_Rig_Monitoring_Architecture.md` (100KB, core — preserved, not archived). README links to it rather than replicating 400+ lines.

### Section: Refactor / Defenses (Self-Contained Features Reference)
Added brief bullet list (6 items from `docs/professional-plan-chart-refactor.md`):
- Feature 1: loader contract test
- Feature 2: `Base.generateLabels()` DRY consolidation
- Feature 3: `fetchNetworkData()` + `buildNetworkDatasets()` composable
- Feature 4: `loadCharts()` simplified (`setTimeout` staggering)
- Feature 5: `chart-registry.js` (loader definitions separated)
- Feature 6: `updateClocksOnHtmxSwap()` simplified (direct selector, no `evt.detail`)

### Section: Deployment / Production Notes (Concise Reference)
Brief reference to verified deploy steps (`sync_to_opt.sh` `python3` fix at `e991279`; `staticfiles` rebuild at line 263, 266; `systemctl restart gunicorn`). References `docs/archive-plan.md` (what was cleaned) and `docs/cdn-cache-bypass-plan.md` (`v=2` approach — NOT professional, user-required bypass).

---

## What Will NOT Be Done (To Keep Professional / Brief)

- NO verbose marketing copy.
- NO full architecture diagram pasted (referenced via link to `docs/GPU_Rig_Monitoring_Architecture.md`).
- NO duplication of `docs/professional-prevention.md` content (defense details referenced, not copied).
- NO `README.md` inside `templates/partials/` (already removed; not professional — already cleaned per previous task).
- Only `README.md` edited; no other production/template/code files changed.

---

## Verification Before Push

Per user's rules (`plan → approval → implement on branch → push to branch → user merges`):
- Modify ONLY `README.md`.
- Check: `git diff -- README.md` shows only the new sections; no accidental other changes.
- Syntax: `README.md` is markdown — no build errors.
- Self-contained: commit message `feat(readme): comprehensive feature + chart + report + architecture inventory`.
- Independent: can revert with `git revert <sha>` without affecting chart/code/compaction fixes.
- Never push to `main`: branch `feat/readme-comprehensive` only; user merges.

---

## Cross-Reference Documents (Not Duplicated, Only Referenced)
- `docs/archive-plan.md`: archive categories (core/future/completed).
- `docs/professional-plan-chart-refactor.md`: 6 self-contained feature plans.
- `docs/professional-prevention.md`: 4-layer defense (W001/W004/0052).
- `docs/professional-plan-report-card.md`: safe identity label (`_safe_gpu_label`) + HTMX indicator fix.
- `docs/plan-daily-maintenance.md`: bool max `CAST` defense (`E009`).
- `docs/cdn-cache-bypass-plan.md`: `v=2` approach (NOT professional; user-required).
- Skill reference (`gpu-rig-monitoring` / `references/job-status-chart-route.md`): bool aggregation pattern (`ExpressionWrapper`, `Cast`, `IntegerField`).
