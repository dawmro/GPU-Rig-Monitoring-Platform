# Plan: Move Agent Update Path from GitHub to Server-Hosted (Minimal)

## Problem Statement

Currently the agent's auto-update mechanism (`agent/check_update.py` and `agent_windows/check_update.py`) fetches the latest `run.py` directly from GitHub raw URLs:
- Linux: `https://raw.githubusercontent.com/dawmro/GPU-Rig-Monitoring-Platform/main/agent/run.py`
- Windows: `https://raw.githubusercontent.com/dawmro/GPU-Rig-Monitoring-Platform/main/agent_windows/run.py`

This approach has critical flaws when the server and agents are at different versions.

---

## Current Approach Problems (GitHub-hosted)

### 1. Schema Version Mismatch
- **Server** runs at version X with schema_version Y
- **Agent** updates to GitHub main (version X+n with schema_version Y+m)
- Agent sends payload with new schema fields → Server rejects (unknown schema_version)
- Server expects old schema fields → Agent doesn't send them (removed in update)
- Result: **Data ingestion fails silently or with 400 errors**

### 2. GitHub as Single Point of Failure
- GitHub rate limits, outages, or repo deletion → **All agents cannot update**
- No fallback, no local mirror
- Network restrictions in some environments block GitHub entirely

### 3. No Version Pinning Per Deployment
- All agents globally get the same "latest" from main
- Cannot deploy v1.17 to rig A while rig B stays on v1.15
- Cannot roll back problematic agent versions
- Cannot test new agent version on subset of rigs

---

## Proposed Solution: Direct Server Fetch (No Manifest)

### Key Insight
The server **already has** the agent files via `sync_to_opt.sh`:
- `/opt/monitoring-agent/run.py` (Linux agent)
- `/opt/agent_windows/run.py` (Windows agent)

Nginx serves them at fixed paths. The agent already has `server_endpoint` in config.yaml. Just fetch directly from server — no manifest endpoint needed.

### Architecture
```
┌─────────────────┐      HTTPS        ┌──────────────────┐
│  Monitoring     │  GET /agent/      │   Agent (rig)    │
│   Server        │  run.py           │                  │
│                 │                   │  check_update.py │
│  - Has agent    │                   │                  │
│    files from   │                   │  - Reads         │
│    sync_to_opt  │                   │    server_endpoint│
│  - Nginx serves │                   │    from config   │
│    at fixed     │                   │  - Fetches       │
│    paths        │                   │    run.py from   │
└─────────────────┘                   │    server        │
                                      │  - Compares      │
                                      │    versions      │
                                      │  - Replaces      │
                                      └──────────────────┘
```

### Server-Side (Minimal)
1. **Nginx config**: Add location blocks to serve agent files:
   ```nginx
   location = /agent/run.py {
       alias /opt/monitoring-agent/run.py;
       add_header Content-Type text/plain;
   }
   location = /agent/check_update.py {
       alias /opt/monitoring-agent/check_update.py;
       add_header Content-Type text/plain;
   }
   location = /agent_windows/run.py {
       alias /opt/agent_windows/run.py;
       add_header Content-Type text/plain;
   }
   location = /agent_windows/check_update.py {
       alias /opt/agent_windows/check_update.py;
       add_header Content-Type text/plain;
   }
   ```
2. **That's it** — `sync_to_opt.sh` already updates these files on deploy

### Agent-Side Changes (`check_update.py`)
1. **Config**: Already has `server_endpoint` (e.g., `http://192.168.1.100`)
2. **Construct URLs**:
   - Linux run.py: `{server_endpoint}/agent/run.py`
   - Linux check_update.py: `{server_endpoint}/agent/check_update.py`
   - Windows run.py: `{server_endpoint}/agent_windows/run.py`
   - Windows check_update.py: `{server_endpoint}/agent_windows/check_update.py`
3. **Fetch remote run.py** from server (replaces GitHub fetch)
4. **Fetch remote check_update.py** from server (NEW: self-update)
5. **Extract version & schema_version** from fetched content (same as current GitHub logic)
6. **Compare versions**: remote > local AND same major version
7. **Validate syntax** (py_compile) — same as current
8. **Replace local run.py** — same as current
9. **Replace local check_update.py** — NEW: self-replace atomically
10. **No GitHub fallback** — new version only updates from server

### Bootstrap: How Old Agents Get New check_update.py
- Old `check_update.py` still points to GitHub
- On first run after server deploy, it fetches `run.py` from GitHub (may fail or get wrong version)
- **But**: Add a one-time migration in `run.py` — on startup, if `check_update.py` is old (detect by checking if it contains GitHub URL), fetch new `check_update.py` from server and replace it
- After that, daily `check_update.py` runs use server for both files
- **New `check_update.py` does NOT contain GitHub URLs** — so bootstrap runs once and stops

---

## Why This Is Sufficient

| Concern | Resolution |
|---------|------------|
| Schema mismatch | Server only serves the run.py it currently runs. Agent gets schema_version from that file — guaranteed compatible. |
| Rollback | Admin replaces `/opt/monitoring-agent/run.py` with previous version, runs `sync_to_opt.sh`. Next agent check gets old version. |
| Auth | Files are plaintext Python. No secrets. Public access is fine. |
| Integrity | Syntax validation (py_compile) catches corruption. |
| Version pinning | Each server deployment serves its own agent version. Rig A on server v1.17 gets v1.17 agent; Rig B on server v1.15 gets v1.15 agent. |
| check_update.py self-update | check_update.py fetches and replaces itself from server using same atomic pattern. |
| Old rig migration | run.py detects old check_update.py (GitHub URL present) and bootstraps new one from server on first run. |

---

## Implementation Plan

### Phase 1: Server-Side (Nginx Only)
1. **nginx.conf**: Add four location blocks for `/agent/run.py`, `/agent/check_update.py`, `/agent_windows/run.py`, `/agent_windows/check_update.py`
2. **server_install.sh**: Create `/opt/monitoring-agent/` and `/opt/agent_windows/` with placeholders (DONE)
3. **Deploy**: Run `sync_to_opt.sh` (already copies agent files) + restart nginx

### Phase 2: Agent-Side (`check_update.py` and `run.py`)
1. **check_update.py**: 
   - Replace GITHUB_RAW_URL with server_endpoint + fixed paths
   - Add self-update logic: fetch check_update.py from server, version compare, atomic replace
   - Platform-specific paths: Linux (`/agent/...`) vs Windows (`/agent_windows/...`)
   - Keep all existing logic: version parsing, comparison, syntax validation, atomic replace
   - **No GitHub fallback** — server only
2. **run.py**: Add bootstrap check on startup — if check_update.py contains GitHub URL, fetch new one from server

### Phase 3: Testing & Rollout
1. Test with single rig pointing to test server
2. Verify schema_version in fetched file matches server's serializer
3. Test self-update of check_update.py
4. Test bootstrap migration on old agent
5. Gradual rollout to fleet

---

## Files to Modify

### Server (gpu_monitor/)
- `gpu_monitor/deploy/nginx.conf` — Add 4 location blocks for agent files
- `gpu_monitor/deploy/server_install.sh` — Create agent dirs with placeholders (DONE)

### Agent (agent/, agent_windows/)
- `check_update.py` — Replace GitHub URL with server_endpoint + fixed paths; add self-update
- `run.py` — Add bootstrap migration for old check_update.py
- `config.yaml.example` — No new config options needed

### Documentation
- `docs/DEPLOYMENT_GUIDE.md` — Document new update flow
- `agent/README.md` — Update auto-update section

---

## Acceptance Criteria

1. ✅ Agent fetches run.py from server (not GitHub) using `server_endpoint` from config
2. ✅ Linux agent fetches `/agent/run.py`, Windows fetches `/agent_windows/run.py`
3. ✅ Version comparison works (remote > local, same major)
4. ✅ Syntax validation (py_compile) on downloaded file
5. ✅ Server serves agent files at fixed nginx paths
6. ✅ `sync_to_opt.sh` updates server's agent files automatically (already does)
7. ✅ Admin updates agent by editing workspace, running sync_to_opt.sh (existing workflow)
8. ✅ Fresh install works (bundled initial agent in install.sh)
9. ✅ Rollback works: admin replaces file, runs sync_to_opt.sh
10. ✅ **check_update.py updates itself from server**
11. ✅ **Old rigs bootstrap new check_update.py via run.py on first run**
12. ✅ **New check_update.py does NOT contain GitHub URLs — bootstrap runs once**