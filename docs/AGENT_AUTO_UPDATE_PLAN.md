# Agent Auto-Update Implementation Plan

## 1. Version Detection

### Approach: Compare `__version__` from GitHub raw file

**Rationale:**
- Simple, no GitHub API rate limits (uses raw.githubusercontent.com)
- No authentication needed for public repos
- Single source of truth: `agent/run.py` `__version__` string
- Works identically for Linux and Windows

**Version format:** `MAJOR.MINOR.PATCH` (e.g., `1.2.0`, `1.2.1`, `1.3.0`)
**Windows suffix:** `1.2.0-win` — platform suffix is stripped before comparison

**Comparison logic:**
```python
current = parse_version("1.2.0")      # from local __version__
latest = parse_version("1.2.1")       # from GitHub raw file
if latest > current:
    update_available = True
```

**Alternative approaches considered:**
- GitHub Releases API: Requires releases to be created, rate limited (60/hr unauthenticated)
- Git tags: Same rate limit issues, more complex parsing
- Server-side version endpoint: Requires server changes, adds dependency

**Decision:** Raw file comparison — simplest, most reliable, no rate limits.

---

## 2. Scheduling

### Approach: Randomized daily check via cron (Linux) / Task Scheduler (Windows)

**Rationale:**
- Prevents thundering herd: All rigs don't check at the same time
- No server-side coordination needed
- Each rig picks a random time once at install, then checks daily at that time
- If check fails (network down), next check is ~24 hours later — acceptable

**Linux implementation:**
```bash
# At install time, pick random hour (0-23) and minute (0-59)
HOUR=$((RANDOM % 24))
MINUTE=$((RANDOM % 60))
# Add to crontab: daily check
echo "$MINUTE $HOUR * * * monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/check_update.py >> /var/log/monitoring-agent/update.log 2>&1" >> /etc/cron.d/monitoring-agent-update
```

**Windows implementation:**
```powershell
# At install time, pick random hour and minute
$Hour = Get-Random -Minimum 0 -Maximum 24
$Minute = Get-Random -Minimum 0 -Maximum 60
# Create scheduled task for daily check
schtasks /create /tn "RigAgentUpdate" /tr "python C:\monitoring-agent\check_update.py" /sc daily /st "$($Hour):$Minute" /ru monitoring-agent
```

**Alternative approaches considered:**
- Check on every agent run (every 60s): Too frequent, wastes bandwidth
- Check every N hours: Still synchronized across rigs
- Server-pushed updates: Requires server infrastructure, more complex
- Fixed time (e.g., 3 AM): All rigs hit GitHub at once

**Decision:** Randomized daily — best balance of simplicity, reliability, and load distribution.

---

## 3. Update Mechanism

### Flow:
```
1. check_update.py runs at scheduled time
2. Fetches https://raw.githubusercontent.com/dawmro/GPU-Rig-Monitoring-Platform/main/agent/run.py
3. Parses __version__ from the raw file
4. Compares with local __version__
5. If newer version available:
   a. Downloads new run.py to temp location
   b. Validates downloaded file (syntax check, version matches)
   c. Backs up current run.py
   d. Replaces run.py with new version
   e. Logs update event
   f. Does NOT restart — next cron cycle picks up new code automatically
6. If same or older version: do nothing
7. If download fails: log error, retry next day
```

### Why not restart immediately?
- Agent runs via cron every 60s — next run automatically uses new code
- No need to kill/restart processes
- Avoids race conditions with running agent
- Simpler, more reliable

---

## 4. Edge Cases

| Edge Case | Handling |
|---|---|
| **Network unavailable** | Log error, retry next day. No disruption to current operation. |
| **GitHub raw file returns 404** | Log error, retry next day. |
| **Downloaded file is corrupt** | Syntax validation before replace. Keep backup. Abort update. |
| **Version parse fails** | Log error, skip update. Don't replace working code with unknown. |
| **Disk full** | Check disk space before download. Abort if < 10MB free. |
| **Permission denied** | Log error. Update requires root/agent user write access. |
| **Rollback needed** | Keep `run.py.bak` (last known good). Manual rollback: `cp run.py.bak run.py` |
| **Windows file lock** | Windows agent not running during update (scheduled task, not cron). If locked, skip and retry tomorrow. |
| **Schema version change** | Agent reports schema_version to server. Server handles backward compat. No special handling needed. |
| **Major version breaking change** | Only auto-update within same major version (1.x → 1.y). Major version bumps (1.x → 2.x) require manual install. |
| **Concurrent update + cron run** | Use atomic rename (`os.replace`) to prevent partial writes. |
| **GitHub rate limiting** | Raw files have generous limits. Daily check per rig is negligible load. |

---

## 5. File Structure

```
agent/
├── run.py                    # Main agent (existing)
├── check_update.py           # NEW: Update checker
├── install.sh                # MODIFIED: Add update cron job
└── config.yaml.example       # Existing

agent_windows/
├── run.py                    # Main agent (existing)
├── check_update.py           # NEW: Update checker (Windows version)
└── install.ps1               # MODIFIED: Add update scheduled task
```

---

## 6. Implementation Steps

### Step 1: Create `check_update.py` (Linux)
- Version fetching from GitHub
- Version comparison
- Download + validate + replace logic
- Logging

### Step 2: Create `check_update.py` (Windows)
- Same logic, Windows-specific paths
- No sudoers needed (runs as user who installed)

### Step 3: Modify `install.sh`
- Add random cron job for update check
- Set proper permissions

### Step 4: Modify `install.ps1` (Windows)
- Add random scheduled task for update check

### Step 5: Add version endpoint to server (optional)
- `/api/v1/agent/latest-version` returns latest version
- Allows server to notify agents of available updates
- Future enhancement, not required for initial implementation

---

## 7. Security Considerations

- **HTTPS only:** All GitHub requests use HTTPS
- **Validate before replace:** Syntax check + version verification before overwriting
- **Backup:** Always keep `run.py.bak` for rollback
- **Minimal permissions:** Update script runs as agent user, only writes to agent directory
- **No secrets in update:** Update script doesn't handle API keys or credentials
