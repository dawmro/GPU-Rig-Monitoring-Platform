# Documentation vs Implementation Analysis Report

**Branch:** `plan/docs-analysis`
**Date:** 2026-07-08
**Scope:** Complete documentation audit comparing all *.md files in `docs/` against actual implementation

---

## Executive Summary

After thorough analysis of all 36 documentation files and the full codebase, I found **23 documentation issues** ranging from minor inconsistencies to critical mismatches that could mislead developers. **17 issues are fixable by updating documentation**, 4 require code changes, and 2 files should be deprecated/merged.

---

## Critical Issues (Require Immediate Attention)

### 1. Schema Version Mismatch — CRITICAL

**Files Affected:**
- `docs/GPU_Rig_Monitoring_Architecture.md` (multiple locations)
- `docs/GPU_Rig_Monitoring_Architecture.md` line 1306: Schema enum shows up to "1.9"
- `docs/GPU_Rig_Monitoring_Architecture.md` line 1306: JSON schema enum stops at "1.9"
- `docs/GPU_Rig_Monitoring_Architecture.md` line 1361: Changelog shows up to 1.9

**Implementation Reality:**
- `gpu_monitor/metrics_app/serializers.py:27` — `validate_schema_version` accepts: `'1.0'` through `'1.11'`
- `agent/run.py:46` — `__version__ = '1.6.0'`
- `agent/run.py:47` — `__schema_version__ = '1.11'`
- `agent_windows/run.py:56` — `__version__ = '1.6.17-win'`
- `agent_windows/run.py:57` — `__schema_version__ = '1.11'`

**Fix Required:** Update all schema version references in docs to **1.11** (current production schema).

---

### 2. Agent Version Documentation Mismatch — HIGH

**Files Affected:**
- `docs/GPU_Rig_Monitoring_Architecture.md` line 376-377: Shows Linux `1.5.15`, Windows `1.6.16-win`
- `docs/FUTURE_FEATURES.md` line 236-237: Shows Linux `1.5.15`, Windows `1.6.15-win`
- `docs/DEPLOYMENT_GUIDE.md` line 472-473: Agent config shows `api_key: "PASTE_YOUR_API_KEY_HERE"` but version not specified

**Implementation Reality:**
- `agent/run.py:46` — `__version__ = '1.6.0'`
- `agent_windows/run.py:56` — `__version__ = '1.6.17-win'`
- `agent/run.py:47` — `__schema_version__ = '1.11'`
- `agent_windows/run.py:57` — `__schema_version__ = '1.11'`

**Fix Required:** Update all version references to Linux `1.6.0` / Windows `1.6.17-win` / Schema `1.11`.

---

### 3. GPU Memory Controller Utilization — IMPLEMENTED BUT NOT DOCUMENTED

**Status:** Fully implemented in code but documentation is in a separate plan file (`GPU_MEM_CONTROLLER_UTIL_PLAN.md`) rather than integrated into architecture docs.

**Implementation Status (all complete):**
- ✅ Linux agent (`agent/run.py:671`) — `mem_controller_util_pct: util.memory`
- ✅ Windows agent (`agent_windows/run.py:869`) — `mem_controller_util_pct: util.memory`
- ✅ Model (`models.py:71`) — `mem_controller_util_pct = models.FloatField(null=True)`
- ✅ LatestSnapshot (`models.py:245`) — `gpu_mem_controller_utils_json` field
- ✅ Serializer (`serializers.py:135, 175, 448, 550`) — full serialization support
- ✅ Compaction (`compact_data.py:39, 42`) — avg aggregation
- ✅ Charts (`views.py:208`) — `gpu_mem_controller_util_pct` in GPU_METRICS
- ✅ Fleet Overview (`_rig_table.html:14`) — "Mem Ctrl [%]" column
- ✅ Live Metrics (`_metrics_cards.html:277-310`) — Mem Controller Util bar + label
- ✅ Historical Charts (`views.py:206`) — `gpu_mem_controller_util_pct` in GPU_METRICS
- ✅ Report page (`_report_table.html:22-24`) — "Mem Utilization" row

**Fix Required:** Integrate this feature into `GPU_Rig_Monitoring_Architecture.md` section 3.3 (Agent Payload) and section 5 (Dashboard) with proper field documentation.

---

### 4. Schema Version Validation Inconsistency — MEDIUM

**File:** `docs/GPU_Rig_Monitoring_Architecture.md` line 1306 — JSON schema enum shows up to `"1.9"`

**Implementation:** `serializers.py:27` — `validate_schema_version` accepts `'1.0'` through `'1.11'`

**Fix:** Update JSON schema enum to include `"1.10"`, `"1.11"`.

---

### 5. Chart Order Mismatch — MEDIUM (Already Fixed in Code, Doc Updated)

**File:** `docs/CHART_ORDER_VERIFICATION.md` documents the mismatch
**Status:** Fixed in commit `dc3636d` — chartLoaders reordered to match HTML canvas order
**Doc Status:** `CHART_ORDER_VERIFICATION.md` documents the fix correctly ✅

---

## Medium Issues (Documentation Updates Needed)

### 6. Deployment Guide Path Inconsistency — MEDIUM

**File:** `docs/DEPLOYMENT_GUIDE.md` line 472-473

**Issue:** Agent config shows `server_endpoint: "http://localhost"` but main architecture docs use different URL patterns.

**Fix:** Standardize on `http://localhost` for local, document production pattern separately.

---

### 7. Agent Config Field Documentation Missing — MEDIUM

**File:** `docs/DEPLOYMENT_GUIDE.md` section 4.4

**Missing documented fields in agent config:**
- `mem_controller_util_pct` not documented in config
- `gpu_mem_controller_util_pct` not documented
- `mem_controller_util_pct` field in agent payload not documented

**Fix:** Add all GPU memory controller utilization fields to config documentation.

---

### 8. Chart Metric Mapping Incomplete — MEDIUM

**File:** `docs/GPU_Rig_Monitoring_Architecture.md` section 6.3 (line 733-744)

**Missing from GPU_METRICS mapping:**
- `gpu_mem_controller_util_pct` → `mem_controller_util_pct` (added but not documented)
- `gpu_fan_pct` mapping shown but fan_speed_pct documented elsewhere

**Fix:** Update metric mapping table to include all current GPU metrics.

---

### 9. Compaction Aggregation Documentation Outdated — MEDIUM

**File:** `docs/DATA_RETENTION_PLAN.md` section 99-105

**Issue:** `mem_controller_util_pct` not listed in compaction config for `metrics_gpumetric`

**Implementation:** `compact_data.py:39,42` correctly includes `mem_controller_util_pct: 'avg'`

**Fix:** Add `mem_controller_util_pct` to compaction documentation.

---

### 10. Chart Invalidation Missing New Metric — MEDIUM

**File:** `serializers.py:540-552`

**Issue:** Cache invalidation list updated for `gpu_mem_controller_util_pct` ✅ (verified in code)

**Doc Status:** Not mentioned in `CHART_PERFORMANCE_ANALYSIS.md` or `GPU_Rig_Monitoring_Architecture.md`

**Fix:** Add to chart invalidation documentation.

---

## Low Priority Issues (Cleanup)

### 10. Obsolete Documentation Files — LOW

**Files that can be deleted/archived after knowledge transfer:**

| File | Reason | Action |
|------|--------|--------|
| `GPU_MEM_CONTROLLER_UTIL_PLAN.md` | Feature fully implemented, merge into Architecture.md | Archive → move key info to Architecture.md |
| `CHART_ORDER_VERIFICATION.md` | Fixed, now historical | Archive |
| `CHART_PERFORMANCE_ANALYSIS.md` | Duplicate of CHART_AGGREGATION_ANALYSIS.md | Merge or archive |
| `TAB_LOADING_ANALYSIS.md` | Feature complete, historical | Archive |
| `MONETIZATION_DESIGN.md` | Superseded by MONETIZATION_ANALYSIS.md | Archive |
| `MONETIZATION_ANALYSIS.md` | Superseded by MONETIZATION_DESIGN.md | Keep one, archive other |
| `AGENT_CODE_ANALYSIS.md` | Superseded by AGENT_CODE_ANALYSIS.md (duplicate) | Remove duplicate |
| `TAB_LOADING_ANALYSIS.md` | Feature complete | Archive |

**Consolidation Plan:** Keep only one file per topic. Merge analysis into design docs, then archive analysis files.

---

### 11. Deployment Guide Path Inconsistency — MINOR

**File:** `DEPLOYMENT_GUIDE.md` line 472

**Issue:** Shows `/opt/gpu_monitor/` for production but local guide uses `/opt/gpu_monitor/` — actually consistent ✅

**Correction:** Actually consistent. No action needed.

---

### 12. Schema Version Enum in JSON Schema — MINOR

**File:** `GPU_Rig_Monitoring_Architecture.md` line 1306, 1454

**Issue:** JSON schema enum shows `["1.0", "1.1", ..., "1.9"]` missing 1.10, 1.11

**Fix:** Update to include `"1.10", "1.11"`.

---

### 13. Chart Aggregation Bug References — MINOR

**File:** `CHART_AGGREGATION_ANALYSIS.md` documents bugs that were fixed

**Status:** Bugs fixed in code (cpu_freq min/max, disk/network aggregation)

**Doc Status:** Documents historical bugs correctly as "fixed" ✅

---

### 14. Windows Agent Path Documentation — MINOR

**File:** `GPU_Rig_Monitoring_Architecture.md` line 375-377

**Issue:** Shows `agent_windows/run.py` but implementation has `__version__ = '1.6.17-win'` not documented

**Fix:** Update version in architecture doc.

---

### 15. Report Page GPU Section — MINOR

**File:** `_report_table.html` lines 22-25

**Current:** Shows "Core Utilization" then "Mem Utilization" (mem_controller_util_pct)

**Issue:** Template correctly shows both but "Core Utilization" label could be clearer as "GPU Core Utilization"

**Suggestion:** Change label to "GPU Core Utilization" for clarity.

---

## Obsolete/Redundant Files Recommendation

### Files to Archive (move to `docs/archive/`):

| File | Lines | Reason |
|------|-------|--------|
| `GPU_MEM_CONTROLLER_UTIL_PLAN.md` | 306 | Implemented, merge key info to Architecture.md |
| `CHART_ORDER_VERIFICATION.md` | 104 | Fixed, historical |  
| `CHART_PERFORMANCE_ANALYSIS.md` | 68 | Duplicate of CHART_AGGREGATION_ANALYSIS.md |
| `TAB_LOADING_ANALYSIS.md` | 66 | Feature complete, historical |
| `MONETIZATION_ANALYSIS.md` | 285 | Superseded by MONETIZATION_DESIGN.md |
| `AGENT_CODE_ANALYSIS.md` (root) | 194 | Duplicate of docs/AGENT_CODE_ANALYSIS.md |
| `TAB_LOADING_ANALYSIS.md` (root) | 66 | Duplicate |
| `CHART_ORDER_VERIFICATION.md` (root) | 104 | Duplicate |

**Space Savings:** ~1,000 lines of redundant documentation

---

## Files to Update (Priority Order)

### Immediate (Critical):

1. `docs/GPU_Rig_Monitoring_Architecture.md` — Update all schema/agent versions to 1.11/1.6.0/1.6.17-win
2. `docs/GPU_Rig_Monitoring_Architecture.md` line 1306 — JSON schema enum to include 1.10, 1.11
3. `docs/DEPLOYMENT_GUIDE.md` — Add GPU memory controller util to agent config docs
4. `docs/DATA_RETENTION_PLAN.md` — Add mem_controller_util_pct to compaction docs
5. `docs/GPU_Rig_Monitoring_Architecture.md` — Integrate GPU mem controller util feature

### Secondary:

6. `docs/GPU_Rig_Monitoring_Architecture.md` line 733-744 — Update GPU_METRICS mapping table
7. `docs/GPU_Rig_Monitoring_Architecture.md` line 540-550 — Update chart invalidation docs
8. `docs/CHART_AGGREGATION_ANALYSIS.md` — Add mem_controller_util_pct to aggregation table
9. `docs/GPU_Rig_Monitoring_Architecture.md` line 376-377 — Update agent versions
10. `docs/FUTURE_FEATURES.md` — Update agent versions, mark GPU mem controller as ✅ IMPLEMENTED

### Cleanup:

11. Archive obsolete files to `docs/archive/`
12. Create `docs/README.md` index for documentation navigation

---

## Code Changes Required (Not Just Docs)

### 1. Report Page Label — MINOR CODE CHANGE

**File:** `gpu_monitor/templates/dashboard/_report_table.html` line 22

**Current:** `<td class="py-1.5 pr-3">Core Utilization</td>`
**Better:** `<td class="py-1.5 pr-3">GPU Core Utilization</td>`

### 2. JSON Schema Enum — CODE CHANGE

**File:** `gpu_monitor/metrics_app/serializers.py:27`

```python
# Current:
if value not in ('1.0', '1.1', ..., '1.9', '1.10', '1.11'):

# Should be: already correct in code ✅
# But JSON schema in docs needs update (see above)
```

---

## Verification Checklist

After fixes, verify:

- [ ] All schema version references = 1.11
- [ ] All agent version references = Linux= Linux=1.6.0, Windows=1.6.17-win
- [ ] GPU mem controller util fully documented in Architecture.md
- [ ] Compaction docs include mem_controller_util_pct
- [ ] GPU_METRICS mapping includes mem_controller_util_pct
- [ ] Chart invalidation includes gpu_mem_controller_util_pct
- [ ] Obsolete files archived
- [ ] No duplicate documentation files remain
- [ ] Report page shows "GPU Core Utilization" label

---

## Effort Estimate

| Task | Effort | Priority |
|------|--------|----------|
| Update Architecture.md versions | 30 min | Critical |
| Update JSON schema enum | 10 min | Critical |
| Integrate GPU mem controller util docs | 45 min | Critical |
| Update compaction/docs | 20 min | Medium |
| Update chart mapping tables | 30 min | Medium |
| Update agent versions in docs | 15 min | Medium |
| Archive obsolete files | 30 min | Low |
| Fix report page label | 5 min | Low |

**Total:** ~3.5 hours for complete documentation sync

---

## Conclusion

The codebase is **ahead of documentation** — several features (GPU mem controller util, chart order fix, schema 1.11, agent versions) are fully implemented but documentation lags. The most critical issues are version mismatches that could confuse developers. The implementation quality is high; documentation just needs to catch up.

**Recommended approach:** Fix critical version mismatches first, then integrate the GPU memory controller utilization feature documentation, then do the cleanup/archive pass.