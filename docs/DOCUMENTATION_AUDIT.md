# Documentation Audit Report

**Date:** 2026-06-17
**Branch:** `fix/windows-disk-io`

---

## Document Evaluation

### ✅ KEEP — Core Architecture & Reference

| Document | Size | Status | Notes |
|---|---|---|---|
|| `GPU_Rig_Monitoring_Architecture.md` | 65KB | ✅ Current | Main architecture reference. v1.5 with disk I/O monitoring (read/write/IOPS/utility), cumulative totals in LatestSnapshot, wmic drive mapping for Windows. Agent versions: Linux 1.5.9, Windows 1.6.10-win, schema 1.7. |
| `DATA_FLOW_ANALYSIS.md` | 14KB | ✅ Current | Complete payload-to-DB field mapping. Updated with LatestSnapshot as primary display data source. |
| `DATA_RETENTION_PLAN.md` | 8KB | ✅ Current | Retention strategy, compaction, cleanup. Still valid. |
| `LOCAL_DEPLOYMENT_GUIDE.md` | 37KB | ✅ Current | Updated models.py description. |
| `DEPLOYMENT_GUIDE.md` | 37KB | ✅ Current | Deployment steps still valid. |

### ✅ KEEP — Operational Guides

| Document | Size | Status | Notes |
|---|---|---|---|
| `BACKFILL_ANALYSIS.md` | 7KB | ✅ Current | Backfill command exists and works. |
| `INGEST_PERFORMANCE_ANALYSIS.md` | 6KB | ✅ Current | Detailed ingest performance measurements. |
| `CHART_PERFORMANCE_ANALYSIS.md` | 2KB | ✅ Current | Updated Live Metrics description. |

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

- **Total documents:** 12
- **Keep:** 12
- **Deleted:** 2 (DOCUMENTATION_UPDATE_PLAN.md, DOC_VS_CODE_DISCREPANCIES.md — working documents)

All remaining documentation is accurate and up-to-date.

---

## Changes in This Branch

1. **Disk I/O monitoring:** Added read_bytes, write_bytes, read_iops, write_iops, busy_time_ms to storage payload (schema 1.7)
2. **Cumulative totals in LatestSnapshot:** 4 new JSON arrays for Total Read/Write/IOPS display
3. **format_bytes_total filter:** New template filter for human-readable sizes
4. **Windows wmic drive mapping:** Reliable partition-to-physical-disk mapping using wmic CLI
5. **Fleet Overview:** Added Disk Util [%] column
6. **Live Metrics storage card:** Shows Total Read/Write (cumulative), Since last update (delta), IOPS, Utilization%
7. **Documentation:** Updated all docs to reflect schema 1.7 changes
