# Archive Plan — Old Docs Not Relevant

Branch: `archive/old-docs` (self-contained; user merges; never `main`).
Status: IMPLEMENTING — reading/analyzing docs directory.

---

## Analysis (Read-Only, No Deletions Yet)

From `docs/` inspection (see workspace):

### Core/Active Documents (DO NOT ARCHIVE)
- `plan-daily-maintenance.md` — current fix plan (bool max, active)
- `professional-plan-chart-refactor.md` — active feature plan
- `professional-plan-report-card.md` — active plan
- `professional-prevention.md` — defense documentation
- `cdn-cache-bypass-plan.md` — current CDN workaround doc
- `js-import-inventory.md` — inventory of loader references

### Old/Completed Plans (CANDIDATE FOR ARCHIVE)
- `ADMIN_TRANSFER_REVISED.md` (3.4KB) — completed feature reference
- `AGENT_AUTO_UPDATE_PLAN.md` (6.4KB) — completed plan
- `AGENT_CODE_ANALYSIS.md` (8KB) — analysis completed
- `AGENT_SERIALIZER_OPTIMIZATION.md` (3.7KB) — completed
- `API_KEY_TRANSFER_ANALYSIS.md` (6.8KB) — analysis completed
- `API_KEY_TRANSFER_IMPL.md` (2.7KB) — completed
- `BACKFILL_ANALYSIS.md` (7.2KB) — completed analysis
- `CHART_AGGREGATION_ANALYSIS.md` (7.4KB) — completed
- `COMPACTION_BUCKETS_PLAN.md` (10KB) — implemented (compact_data.py verified)
- `DATA_FLOW_ANALYSIS.md` (17.6KB) — completed
- `DATA_RETENTION_ANALYSIS.md` (5.6KB) — completed
- `DATA_RETENTION_PLAN.md` (11.2KB) — implemented (`cleanup_old_data.py` verified)
- `DEPLOY.md` (5.3KB) — may be superseded by `DEPLOYMENT_GUIDE.md`
- `DEPLOYMENT_GUIDE.md` (58KB) — core reference; DO NOT ARCHIVE
- `FLEET_OVERVIEW_DESIGN.md` (23KB) — design reference; DO NOT ARCHIVE
- `FUTURE_FEATURES.md` (42KB) — future plans; may archive individual completed items
- `GPU_Rig_Monitoring_Architecture.md` (100KB) — CORE; DO NOT ARCHIVE (same for .docx/.pdf)
- `LAYOUT_OPTIMIZATION_PLAN.md` (14KB) — completed optimization
- `LOADED_REFRESHED_PLAN.md` (8.3KB) — completed
- `LOCAL_DEPLOYMENT_GUIDE.md` (41KB) — core deploy reference; DO NOT ARCHIVE
- `LOG_ANALYSIS.md` (2.5KB) — completed analysis
- `POSSIBLE_FUTURE_WORK_TIMESCALEDB.md` (32KB) — future; DO NOT ARCHIVE
- `TIMESCALEDB_VS_OUR_APPROACH.md` (6.8KB) — comparison doc; may archive
- `TRANSFER_SECURITY.md` (7.6KB) — completed
- `VACUUM_ANALYSIS.md` (5.8KB) — completed

### Sub-Directories (Already Archived / Not Relevant)
- `docs/archive/` — `AUDIT_LOG_PLAN.md`, `CHART_ORDER_VERIFICATION.md`, `CHART_PERFORMANCE_ANALYSIS.md`, `GPU_MEM_CONTROLLER_UTIL_PLAN.md`, `INGEST_PERFORMANCE_ANALYSIS.md`, `LOG_ROTATION_EDGE_CASES.md`, `MONETIZATION_ANALYSIS.md`, `PLAN_chart_job_status.md`, `TAB_LOADING_ANALYSIS.md` — these are ALREADY archived; leave as-is.
- `docs/future_plans/` — `CELERY_*`, `DOCKER_MANIFEST_EXTENSION_PLAN.md`, `DOCS_ANALYSIS_REPORT.md`, `MONETIZATION_DESIGN.md`, `POWER_CONSUMPTION_PLAN.md`, `REMOTE_TERMINAL_IMPLEMENTATION.md` — future/planned; DO NOT ARCHIVE (still relevant for planning).

---

## Implementation Plan (Self-Contained Per User Requirement)

Each file moved as separate commit (`feat(archive): move <filename>`). Order: easiest (smallest completed analysis docs) → harder (larger completed plans, then future docs if needed).

Before moving any file: verify it is NOT referenced by active code, skills, or docs (e.g., `DEPLOY.md` referenced by `DEPLOYMENT_GUIDE.md` — may need to merge or keep).

After archive: verify `docs/archive/` exists and moved files preserved (no deletion — archive = preserve, not delete).
