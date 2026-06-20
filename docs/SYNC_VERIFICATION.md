# Sync Verification Report

## Files Changed in Latest Commits (HEAD~3..HEAD)

| File | Workspace | Server | Match |
|------|-----------|--------|-------|
| `gpu_monitor/accounts/urls.py` | ✅ | ✅ | ✅ |
| `gpu_monitor/accounts/views.py` | ✅ (9500 bytes, 21:42) | ✅ (9500 bytes, 21:42) | ✅ |
| `gpu_monitor/metrics_app/views.py` | ✅ (17538 bytes, 21:02) | ✅ (17538 bytes, 21:02) | ✅ |
| `gpu_monitor/rigs/migrations/0004_rig_enrolled_by_api_key.py` | ✅ | ✅ | ✅ |
| `gpu_monitor/rigs/models.py` | ✅ (2871 bytes, 21:00) | ✅ (2871 bytes, 21:00) | ✅ |
| `gpu_monitor/templates/accounts/_key_row.html` | ✅ (1431 bytes, 21:19) | ✅ (1431 bytes, 21:19) | ✅ |
| `gpu_monitor/templates/accounts/api_keys.html` | ✅ | ✅ | ✅ |

## Sync Script Analysis

### What sync_to_opt.sh copies:
1. **Step 1**: Django app directories (accounts, rigs, metrics_app, etc.) → `/opt/gpu_monitor/`
2. **Step 2**: Templates → `/opt/gpu_monitor/templates/`
3. **Step 2b**: Project-level static files → `/opt/gpu_monitor/static/`
4. **Step 6**: Deploy files + scripts → `/opt/gpu_monitor/deploy/`
5. **Step 8**: Runs `collectstatic` and `migrate` on server

### Server directory structure:
The server uses a FLAT structure:
- `/opt/gpu_monitor/accounts/` (not `/opt/gpu_monitor/gpu_monitor/accounts/`)
- `/opt/gpu_monitor/rigs/`
- `/opt/gpu_monitor/metrics_app/`
- `/opt/gpu_monitor/templates/`
- `/opt/gpu_monitor/static/`

The sync script correctly handles this because:
- `$WORKSPACE/gpu_monitor/accounts/` → `$OPT/gpu_monitor/accounts/` ✅
- `$WORKSPACE/gpu_monitor/templates/` → `$OPT/gpu_monitor/templates/` ✅

### Potential Issues:
1. **Migration not applied**: The `enrolled_by_api_key` field requires migration `0004` to be applied on the server. The sync script runs `migrate` in Step 8, but only if there are pending migrations.
2. **Gunicorn cache**: Old `.pyc` files may be cached. Gunicorn needs to be restarted to pick up changes.
3. **DB password**: Local dev server can't connect to PostgreSQL (wrong password), so I couldn't verify the migration was applied.

## Code Fixes Applied

1. **Missing `timezone` import** in `accounts/views.py` — `revoke_api_key()` used `timezone.now()` without importing it
2. **HTMX response** — `revoke_api_key()` and `reactivate_api_key()` now return rendered `_key_row.html` instead of empty response
3. **New `_key_row.html`** partial template for HTMX responses
4. **`enrolled_by_api_key` FK** on Rig model to track which API key enrolled each rig
5. **`reactivate_api_key()`** view to restore revoked keys
6. **`delete_api_key()`** view with proper HTMX handling
7. **Rig count** displayed next to each key (annotated via `Count('enrolled_rigs')`)
