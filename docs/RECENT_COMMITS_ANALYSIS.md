# Detailed Analysis of Latest 10 Commits (7f1250e → eb09156)

---

## Commit 1: `7f1250e` — fix: correct table name in docs
**File:** `docs/GPU_Rig_Monitoring_Architecture.md`
**Lines changed:** 4 insertions, 4 deletions

Fixed 2 occurrences of wrong table name `metrics_latestsnapshot` → `metrics_latest_snapshot`:
- §6 Data Model Reference table (line 624)
- §11.5 Ingest Pipeline write operations table (line 921)

**Why it was wrong:** Typo — missing 's' in 'latest'. The Django model's `db_table` has always been `metrics_latest_snapshot`.

---

## Commit 2: `1a860a1` — fix: rename database table
**Files:** `migrations/0035_rename_latestsnapshot_table.py` (new), `docs/TABLE_NAME_FIX.md` (new)
**Lines changed:** 70 insertions

**Problem:** The actual PostgreSQL table was named `metrics_latestsnapshot` but Django expected `metrics_latest_snapshot`. This caused 500 errors:
```
relation "metrics_latestsnapshot" does not exist
LINE 1: SELECT * FROM "metrics_latestsnapshot" LIMIT 1
```

**Fix:** Created migration with `ALTER TABLE IF EXISTS metrics_latentsnapshot RENAME TO metrics_latest_snapshot`

**Why it happened:** The table was created with the wrong name at some point (likely during initial setup), and subsequent migrations added columns to it but never fixed the name.

---

## Commit 3: `84c3530` — Merge pull request #104 (docs/update-all-docs)
**What:** Merged 7 documentation commits into main:
- Rolling error history with deduplication (1000 errors, sha256 fingerprint dedup)
- CPU frequency collection/display on agent and server
- Template responsive height fix (calc(100vh - 250px))
- Table name fixes in docs
- Database table rename migration

---

## Commit 4: `2b453e3` — fix: limit_req_zone context and HTTPS block
**File:** `gpu_monitor/deploy/nginx.conf`
**Lines changed:** 1 insertion, 24 deletions

**Problems fixed:**
1. `limit_req_zone` was inside `server` block — must be in `http` context
2. HTTPS block referenced Let's Encrypt certs that don't exist on fresh server

**What was removed:**
- Entire HTTPS server block (port 443)
- SSL certificate paths (`/etc/letsencrypt/live/monitor.example.com/`)
- Rate limit zones (`limit_req_zone`)
- Security headers (HSTS, CSP, X-Frame-Options)

**What remains:** Simple HTTP→Gunicorn proxy on port 80

**Impact:** Fresh servers work without TLS. HTTPS must be configured separately via Certbot.

---

## Commit 5: `51f4b29` — fix: sync all deploy files
**File:** `scripts/sync_to_opt.sh`
**Lines changed:** 18 insertions, 13 deletions

**Problem:** Only copied `*.sh` files from deploy/. `gunicorn.service` and `nginx.conf` were NOT synced.

**Fix:** Changed from `for script in "$WORKSPACE/gpu_monitor/deploy/"*.sh` to `for f in "$WORKSPACE/gpu_monitor/deploy/"*`

Scripts get `chmod +x`, config files get `chmod 644`.

**Impact:** All deploy files now synced: `gunicorn.service`, `nginx.conf`, `server_install.sh`, `data_retention.sh`, `update_rig_status.sh`

---

## Commit 6: `d5377f6` — Merge pull request #105
Merged the sync fix into main.

---

## Commit 7: `f84dcfc` — Update server_install.sh to be rerunnable
**File:** `gpu_monitor/deploy/server_install.sh`
**Lines changed:** 188 insertions, 25 deletions

**Key additions:**
- Server IP detection from `hostname -I`
- `DJANGO_ALLOWED_HOSTS_VALUE` construction (domain + localhost + server IP)
- `CSRF_TRUSTED_ORIGINS_VALUE` construction
- Idempotent PostgreSQL setup (`CREATE USER IF NOT EXISTS`)
- `.env` file creation with security settings
- Firewall (UFW) configuration
- Certbot TLS certificate request
- Nginx + Gunicorn systemd setup
- Preserves existing `.env` secrets on rerun

**Why:** Original script was one-time-only. Now safe to re-run for updates.

---

## Commit 8: `6cba81e` — Update security for settings.py
**File:** `gpu_monitor/gpu_monitor/settings.py`
**Lines changed:** 55 insertions, 13 deletions

**Changes:**
- Added `env_bool()` and `env_list()` helper functions
- All security settings now controlled via `.env`:
  - `DEBUG` → `env_bool("DJANGO_DEBUG", True)`
  - `ALLOWED_HOSTS` → `env_list("DJANGO_ALLOWED_HOSTS", [...])`
  - `CSRF_TRUSTED_ORIGINS` → `env_list("CSRF_TRUSTED_ORIGINS", [])`
  - Added 15+ new security settings (SSL redirect, cookie secure, HSTS, etc.)

**Why:** Hardcoded settings prevented per-environment configuration.

---

## Commit 9: `e1aad56` — Update server_install.sh with security envs
**File:** `gpu_monitor/deploy/server_install.sh`
**Lines changed:** 182 insertions, 77 deletions

Updated `server_install.sh` to write all new security env vars to `.env` during installation.

**Impact:** New server installations get proper security defaults automatically.

---

## Commit 10: `e5ec231` — Update DEPLOYMENT_GUIDE.md
**File:** `docs/DEPLOYMENT_GUIDE.md`
**Lines changed:** 17 insertions, 1 deletion

Added all new security env vars to the example `.env` file in the deployment guide.

---

## Summary of Files Changed

| File | Commits | Nature |
|---|---|---|
| `settings.py` | 6cba81e | Security hardening via env vars |
| `server_install.sh` | f84dcfc, e1aad56 | Rerunnable + security envs |
| `nginx.conf` | 2b453e3 | Simplified (HTTP-only) |
| `DEPLOYMENT_GUIDE.md` | e5ec231 | Security env docs |
| `sync_to_opt.sh` | 51f4b29 | Sync all deploy files |
| `agent/install.sh` | eb09156 | Remove data retention |
| `metrics_app/models.py` | 2db83f6, 1a860a1 | cpu_freq fields + table rename |
| `metrics_app/serializers.py` | 92c418d, 59f902f | cpu_freq + error history |
| `metrics_app/migrations/` | 0034, 0035 | New fields + table rename |
| `GPU_Rig_Monitoring_Architecture.md` | 7f1250e | Table name fix |

---

## Key Observations

1. **Security was the main theme** — 3 commits focused on moving hardcoded settings to env vars
2. **Idempotency** — server_install.sh can now be re-run safely
3. **sync_to_opt.sh now copies everything** — including .service and .conf files
4. **agent/install.sh cleaned up** — removed data retention (server-side concern)
5. **nginx.conf simplified** — HTTP-only, HTTPS must be configured separately
6. **CPU frequency feature** — full pipeline from agent collection to chart display
7. **Error history** — rolling buffer with deduplication replaces overwrite-once behavior
