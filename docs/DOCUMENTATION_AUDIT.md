# Documentation Audit Report

**Date:** 2026-06-19
**Branch:** `docs/update-all-docs`

---

## Document Evaluation

### ✅ KEEP — Core Architecture & Reference

| Document | Size | Status | Notes |
|---|---|---|---|
| `GPU_Rig_Monitoring_Architecture.md` | 70KB | ⚠️ Needs update | Agent versions, schema 1.9, CPU frequency fields, error history fields need updating. |
| `DATA_FLOW_ANALYSIS.md` | 17KB | ⚠️ Needs update | CPU frequency fields in MetricSnapshot, error_history_json in Rig table. |
| `DATA_RETENTION_PLAN.md` | 8KB | ✅ Current | Retention strategy, compaction, cleanup, VACUUM ANALYZE. |
| `LOCAL_DEPLOYMENT_GUIDE.md` | 40KB | ✅ Current | Updated with email config, password recovery, log file creation, agent directory permissions. |
| `DEPLOYMENT_GUIDE.md` | 56KB | ✅ Current | Updated with email config, VACUUM ANALYZE, comprehensive security hardening, log permission fixes, agent permission fixes. |

### ✅ KEEP — Operational Guides

| Document | Size | Status | Notes |
|---|---|---|---|
| `BACKFILL_ANALYSIS.md` | 7KB | ✅ Current | Backfill command exists and works. |
| `INGEST_PERFORMANCE_ANALYSIS.md` | 6KB | ✅ Current | Detailed ingest performance measurements. |
| `CHART_PERFORMANCE_ANALYSIS.md` | 2KB | ⚠️ Needs update | New CPU Frequency chart. |
| `VACUUM_ANALYSIS.md` | 6KB | ✅ Current | PostgreSQL VACUUM FULL vs VACUUM ANALYZE analysis and recommendations. |

### ✅ KEEP — Design Context & Plans

| Document | Size | Status | Notes |
|---|---|---|---|
| `TIMESCALEDB_VS_OUR_APPROACH.md` | 7KB | ✅ Relevant | Architectural context for compaction vs TimescaleDB. |
| `POSSIBLE_FUTURE_WORK_TIMESCALEDB.md` | 33KB | ✅ Relevant | Detailed migration plan from current PostgreSQL to TimescaleDB. Reference for future scaling work. |
| `PCIE_LINK_PLAN.md` | 6KB | ✅ Relevant | PCIe fields implemented. Reference for data collection. |
| `AGENT_AUTO_UPDATE_PLAN.md` | 6KB | ✅ Relevant | check_update.py exists, install scripts don't set up cron yet. |

---

## Documents Deleted

| Document | Size | Reason |
|---|---|---|
| `CHART_PLAN_ORDERED.md` | 7KB | All charts fully implemented. Superseded by Architecture doc §5. |
| `ADDITIONAL_CHARTS_PROPOSAL.md` | 8KB | All proposed charts implemented. Superseded by Architecture doc §5. |
| `LIVE_METRICS_PLAN.md` | 2KB | All features implemented. Data source mapping in DATA_FLOW_ANALYSIS.md. |

---

## Summary

- **Total documents:** 13
- **Keep:** 13
- **Deleted:** 2 (DOCUMENTATION_UPDATE_PLAN.md, DOC_VS_CODE_DISCREPANCIES.md — working documents)

---

## Changes in This Branch

1. **Updated all docs to reflect latest code changes:**
   - Agent versions: Linux 1.5.13, Windows 1.6.14-win
   - Schema version: 1.9 (CPU frequency fields added)
   - CPU frequency: 3 new FloatFields on MetricSnapshot and LatestSnapshot
   - Error history: `error_history_json` (1000 errors) + `_seen_error_hashes_json` (dedup) on Rig
   - New CPU Frequency chart in rig detail
   - Errors tab now shows full history (responsive height)
