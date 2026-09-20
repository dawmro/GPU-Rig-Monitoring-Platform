# Deploy Contract

> **TL;DR**: Any commit that adds/modifies a file under `gpu_monitor/static/` MUST be followed by `python manage.py collectstatic --noinput` on the production server (`/opt/gpu_monitor/`). Skipping this step silently breaks the deployed UI — nginx returns 404 for the new file, all elements styled by the new file lose their formatting, and the failure looks like a CSS bug but isn't.

---

## The Contract (in one sentence)

**`/opt/gpu_monitor/staticfiles/` is the deployed copy of `/opt/gpu_monitor/static/`. They MUST stay in sync. Only `collectstatic` keeps them in sync. Only `sync_to_opt.sh` runs `collectstatic`. Therefore: any change to `gpu_monitor/static/*.css|js|img|svg|...` requires running `sync_to_opt.sh` (or running `collectstatic` manually).**

---

## The bug this contract prevents

**Symptoms** (what the user sees):
- New CSS classes appear in the page source (`class="grm-input"`, `class="grm-btn-primary"`, etc.)
- But elements with those classes have no formatting — invisible text, white-on-white inputs, unstyled buttons
- Browser DevTools → Network shows `/static/css/app.css` returning **404**
- DevTools → Console shows no CSS errors (because the file just doesn't load)

**Root cause**:
- Templates reference `<link rel="stylesheet" href="/static/css/app.css">`
- Nginx serves `/static/*` from `/opt/gpu_monitor/staticfiles/` (see `deploy/nginx.conf`)
- `staticfiles/css/app.css` doesn't exist because `collectstatic` was never run
- The source file at `gpu_monitor/static/css/app.css` exists, the templates exist, but the deployed collection is stale

**Why it happens**:
- `collectstatic` was originally nested inside the "if migrations needed" branch of `sync_to_opt.sh`
- Code-only / template-only / static-only commits (no model changes) skip that branch
- Result: the deploy script's logic was correct for "no DB change → nothing to do" but wrong for "no DB change → still need to re-collect static"

**Fix** (applied in `scripts/sync_to_opt.sh` and `scripts/sync_and_migrate.sh`):
- `collectstatic` is now called on **every** deploy, unconditionally
- It uses `--clear` so renamed/deleted files don't linger

---

## What you need to do for each kind of commit

| Commit type | What happens | What you do |
|---|---|---|
| **Code only** (views, models, Python) | Templates + static unchanged | Run `sync_to_opt.sh` (migrations handled automatically) |
| **Models** (schema change) | Migrations created and applied | Run `sync_to_opt.sh` (handles makemigrations + migrate + collectstatic + gunicorn restart) |
| **Templates** (`.html` files) | No code change, no migration | Run `sync_to_opt.sh` (collectstatic still runs — harmless if no static changed) |
| **Static files** (`.css`, `.js`, images in `gpu_monitor/static/`) | Templates may reference the new files | Run `sync_to_opt.sh` — **mandatory**, this is the bug case |
| **CSS class additions** (Phase 0.1 style) | Templates reference new classes; CSS file has the rules | Run `sync_to_opt.sh` — otherwise nginx 404s the CSS and the page looks unstyled |

**TL;DR for the day-to-day: just always run `sync_to_opt.sh`.** It now does the right thing in every case.

---

## Recovery: what to do if you forgot

**Symptom**: a new `<link>` to a CSS/JS file is in the deployed page source, but the file returns 404.

**Fix** (one command, non-destructive):
```bash
cd /opt/gpu_monitor && source venv/bin/activate
python manage.py collectstatic --noinput --clear
sudo systemctl restart gunicorn   # only if you changed templates; not required for pure static
```

**Verify the fix**:
```bash
ls -la /opt/gpu_monitor/staticfiles/css/app.css  # should exist
curl -sI http://localhost/static/css/app.css     # should be 200
```

**Rollback** (if collectstatic causes an issue):
```bash
rm -rf /opt/gpu_monitor/staticfiles/css/<problematic-file>
```
Worst case: 404 on that file again — same as before the fix, no data loss.

---

## Defense in depth: Django system check

In addition to the deploy script, the `dashboard` app runs a Django system check on every `manage.py check` (and implicitly on every `manage.py runserver` / `migrate` / `collectstatic`) that validates all template `class="..."` attributes against the actual CSS files in `gpu_monitor/static/css/`. See `dashboard/checks.py` for the implementation.

This means:
- Adding a CSS class to a template without defining it in `app.css` → `manage.py check` fails in CI
- Renaming a CSS class in `app.css` without updating templates → `manage.py check` fails in CI
- Catching this in `manage.py check` is **before** `collectstatic`, so the broken state never reaches production

The system check does NOT verify Tailwind utility classes (e.g. `text-gray-400`) because those are provided by the CDN at runtime. It only verifies our own `grm-*` classes.

---

## Related documentation

- `docs/DEPLOYMENT_GUIDE.md` — full production deploy walkthrough
- `docs/LOCAL_DEPLOYMENT_GUIDE.md` — local dev setup
- `deploy/nginx.conf` — nginx config showing the `/static/` → `/opt/gpu_monitor/staticfiles/` mapping
- `scripts/sync_to_opt.sh` — the deploy script
- `scripts/sync_and_migrate.sh` — legacy manual-copy variant
- `dashboard/checks.py` — Django system check
