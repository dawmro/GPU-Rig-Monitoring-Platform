# Docs-vs-Code Alignment Report

## Discrepancies Found

### 1. LOG_ANALYSIS.md — Out of date
**Issue:** Still references old 5-file logrotate config
- Lists only 5 log files (missing cleanup-cron.log and update.log)
- Shows old `copytruncate` approach instead of `create` + `postrotate`
- Doesn't mention `rig_status.log`
**Severity:** MEDIUM — documentation doesn't match current implementation

### 2. AUDIT_LOG_PLAN.md — Partially out of date
**Issue:** Template design in doc differs from actual implementation
- Doc describes table-based layout (Timestamp, Action, Target, Details, IP columns)
- Actual implementation uses card-based layout with action badge + description
- Doc shows `{{ log.action|title }}` format, actual uses raw `{{ log.action }}`
- Doc doesn't mention `_log_description.html` partial template
- Doc doesn't mention `audit_tags.py` for DB lookup fallback
- Doc shows `max-w-4xl` width, actual uses `max-w-[95%]`
- Doc page template at line 201 shows old `max-w-4xl`, should be `max-w-[95%]`
**Severity:** MEDIUM — implementation diverged from plan during iteration

### 3. GPU_Rig_Monitoring_Architecture.md — Minor staleness
**Issue:** Section 4.3 (Ingestion Pipeline) says `IngestSerializer validation (schema version 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, or 1.6)` but code supports through 1.9
**Severity:** LOW — cosmetic

### 4. FLEET_OVERVIEW_MOBILE_PLAN.md — Verify implementation matches
**Issue:** Plan says breakpoint is `md:` (768px) but original issue was <896px
**Severity:** LOW — plan is correct, just verifying

### 5. NAV_BAR_MOBILE_PLAN.md — Activity link was missing from mobile menu
**Issue:** Plan shows Activity link in mobile code, but it was actually missing from the template
**Status:** FIXED — Activity link added to mobile menu

### 6. Chart aggregation table in CHART_AGGREGATION_ANALYSIS.md
**Issue:** Table at line 89 shows "Disk read/write bytes: AVG of per-min deltas → AVG of hourly SUMs" but this is for 24h chart (raw data). The 7d/30d should show SUM for disk I/O.
**Severity:** LOW — explanation is correct but could be clearer

### 7. New files not documented in architecture
**Issue:** New files created in feature branch not mentioned in architecture doc:
- `audit/views.py` — Activity feed view
- `audit/templatetags/audit_tags.py` — Template tag for target name lookup
- `audit/management/commands/backfill_audit_names.py` — Backfill command
- `audit/urls.py` — Audit URL routing
- `templates/audit/audit_log.html` — Activity feed template
- `templates/audit/_log_description.html` — Log description partial
- `docs/LOG_ROTATION_EDGE_CASES.md` — Log rotation analysis
- `docs/FLEET_OVERVIEW_MOBILE_PLAN.md` — Mobile layout plan
- `docs/NAV_BAR_MOBILE_PLAN.md` — Nav bar mobile plan
**Severity:** MEDIUM — architecture doc should reference new files

### 8. Obsolete docs to consider deleting
- `DOC_AUDIT_REPORT.md` — One-time audit, knowledge transferred
- `ARCHITECTURE_AUDIT.md` — One-time audit, knowledge transferred
- `ENROLLED_BY_KEY_ANALYSIS.md` — Feature implemented, knowledge in architecture doc
- `ADMIN_TRANSFER_REVISED.md` — Implementation reference, could be kept
- `FUTURE_FEATURES.md` — Planning doc, could be kept for reference

## Fixes Needed

### Fix 1: Update LOG_ANALYSIS.md
- Add 2 missing log files (cleanup-cron.log, update.log)
- Update to reflect 7-file logrotate config
- Update rotation details

### Fix 2: Update AUDIT_LOG_PLAN.md
- Update template section to match actual implementation
- Add `_log_description.html` partial description
- Add `audit_tags.py` description
- Update width to `max-w-[95%]`
- Add note about key prefix display for apikey.created

### Fix 3: Update GPU_Rig_Monitoring_Architecture.md
- Add new audit files to key files table
- Add Activity Feed to feature list
- Update schema version range in ingestion pipeline
- Update API endpoints table with new endpoints

### Fix 4: Update FLEET_OVERVIEW_MOBILE_PLAN.md
- Verify implementation matches plan

### Fix 5: Consider deleting obsolete docs
- Delete DOC_AUDIT_REPORT.md
- Delete ARCHITECTURE_AUDIT.md
- Keep ADMIN_TRANSFER_REVISED.md (useful reference)
- Keep FUTURE_FEATURES.md (planning reference)
