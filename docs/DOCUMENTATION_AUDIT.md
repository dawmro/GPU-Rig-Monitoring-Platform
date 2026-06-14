# Documentation Audit Report

**Date:** 2026-06-12
**Branch:** `feature/agent-local-ip`

---

## Document Evaluation

### ✅ KEEP — Core Architecture & Reference

| Document | Size | Status | Notes |
|---|---|---|---|
|| `GPU_Rig_Monitoring_Architecture.md` | 65KB | ✅ Current | Main architecture reference. v1.4 with snapshot-timeseries decoupling, ingest performance, query budgets. Agent versions: Linux 1.5.5, Windows 1.6.5-win, schema 1.6. |
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

- **Total documents:** 12 (down from 14)
- **Keep:** 12
- **Deleted:** 3

All remaining documentation is accurate and up-to-date.

---

## Changes in This Branch

1. **Primary IP in header:** Added `primary_ip` derived from first non-loopback interface's IPv4 (from existing `network_ipv4s_json` data)
2. **Template display:** Shows primary IP below status badge in rig detail header
3. **No agent changes:** IP was already collected per-interface in network data
4. **No model changes:** Uses existing `network_ipv4s_json` — no new fields needed
5. **Redundancy removed:** Original `local_ip` feature was reverted (duplicate of existing Network section data)
6. **Documentation:** Updated audit, removed schema 1.6 changelog and version bumps
