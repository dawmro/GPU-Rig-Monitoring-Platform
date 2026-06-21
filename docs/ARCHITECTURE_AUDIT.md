# Architecture & README Audit — Discrepancies Found

## GPU_Rig_Monitoring_Architecture.md

### Section 3.3 — Payload Schema
- **Doc says:** Schema v1.7 with agent_version 1.5.9
- **Code says:** Schema v1.9, agent Linux 1.5.13 / Windows 1.6.14-win
- **Fix:** Update schema to 1.9, update agent versions

### Section 3.3 — Schema changelog
- **Doc says:** Changelogs for 1.4→1.5, 1.5→1.6, 1.6→1.7, 1.7→1.8
- **Missing:** 1.8→1.9 changelog (cpu_freq fields, error history, top_processes)
- **Fix:** Add 1.8→1.9 changelog entry

### Section 3.5 — Agent versions
- **Doc says:** Linux 1.5.13, Windows 1.6.14-win, schema 1.9
- **Status:** ✅ Correct

### Section 4.3 — Ingestion Pipeline
- **Doc says:** `IngestSerializer validation (schema version 1.0, 1.1, 1.2, 1.3, 1.4, 1.5, or 1.6)`
- **Code says:** Validates 1.0 through 1.9
- **Fix:** Update to include 1.7, 1.8, 1.9

### Section 4.3 — Missing enrolled_by_api_key update
- **Doc says:** `Update Rig.last_seen = now(), Rig.status = ONLINE`
- **Missing:** `enrolled_by_api_key` update on every ingest
- **Fix:** Add enrolled_by_api_key update to pipeline description

### Section 4.6 — API Endpoints
- **Missing:** `/accounts/admin/transfer-keys/` endpoint
- **Missing:** `/accounts/api-keys/<key_id>/reactivate/` endpoint
- **Missing:** `/accounts/api-keys/<key_id>/delete/` endpoint
- **Fix:** Add new endpoints to table

### Section 6.1 — Table Summary
- **Doc says:** `accounts_apikey` — "API keys for agent ingestion (Argon2id hashed)"
- **Missing:** `base_name`, `transfer_count` fields
- **Fix:** Add field descriptions

### Section 6.1 — Rig model
- **Doc says:** `rigs_rig` — "Rig inventory (uuid PK, owner FK, status, last_seen, name, latest_errors_json)"
- **Missing:** `enrolled_by_api_key` FK field
- **Fix:** Add enrolled_by_api_key to description

### Section 6.1b — Management Commands
- **Missing:** `daily_maintenance` command (combines compact + cleanup + vacuum)
- **Fix:** Add to table

### Section 7 — Security
- **Doc says:** "Email backend: Console by development (prints to terminal), Gmail SMTP in production"
- **Status:** ✅ Correct (configurable via env vars)

## README.md (Root)

### Architecture Diagram
- **Doc says:** "HTTPS POST /api/v1/ingest/" and "HTTPS (TLS 1.3)"
- **Code says:** HTTP-only (no TLS in nginx.conf)
- **Fix:** Update to HTTP (no TLS) or note that TLS is optional

### What Gets Collected
- **Missing:** CPU frequency, top processes, error history with deduplication
- **Fix:** Add missing metrics

### Dashboard Features
- **Missing:** API key management, tag management, transfer keys (admin)
- **Fix:** Add missing features

### Security
- **Doc says:** "TLS 1.3 via Let's Encrypt (auto-renewed)"
- **Code says:** HTTP-only deployment
- **Fix:** Update to reflect HTTP-only or note TLS is optional

### Tech Stack
- **Doc says:** "Django 6.x"
- **Code says:** Django 6.0.6
- **Status:** ✅ Close enough

## agent/README.md

### Version
- **Doc says:** "Version: 1.5.7 | Schema: 1.6"
- **Code says:** Version 1.5.13 | Schema: 1.9
- **Fix:** Update version and schema

### What Gets Collected
- **Missing:** CPU frequency, top processes
- **Fix:** Add missing metrics

### GPU Processes
- **Doc says:** "nvidia-smi subprocess"
- **Code says:** pynvml (not subprocess)
- **Fix:** Update description

## agent_windows/README.md

### Version
- **Doc says:** "Version: 1.6.7-win | Schema: 1.6"
- **Code says:** Version 1.6.14-win | Schema: 1.9
- **Fix:** Update version and schema

### What Gets Collected
- **Missing:** CPU frequency, top processes
- **Fix:** Add missing metrics
