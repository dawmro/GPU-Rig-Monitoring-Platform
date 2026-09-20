# CDN Cache Bypass (`v=2`) — NOT Professional, User-Required Workaround

Status: APPLIED (`feat/cdn-cache-bypass-v2` branch) — NOT recommended as permanent; documented here for transparency.
Branch: `feat/cdn-cache-bypass-v2` (self-contained; user merges; never main).

---

## What Was Applied

- `gpu_monitor/templates/dashboard/rig_detail.html`: all chart JS tags use `?v=2` (`chart-base.js?v=2`, `chart-loaders.js?v=2`, `chart-registry.js?v=2`, `chart-runtime.js?v=2`, `rig-detail.js?v=2`).
- `gpu_monitor/templates/base.html`: `app-base.js?v=2`.

Effect: CDN (Cloudflare) and browser treat these as new URLs — guaranteed cache miss. After deploy with rebuilt staticfiles (`sync_to_opt.sh` line 263: `rm -rf staticfiles/*` + `collectstatic --clear`), the `v=2` URL will serve the rebuilt loader.

---

## Why This Is NOT Professional

Per user's direct instruction (user stated explicitly: "this is the best I came up with"; accepted after professional alternatives presented):

1. **Not durable**: `v=2` creates a separate cached entry at CDN; future reverts (removing `v=2`) will serve an OLD cached `v=2` version for ~4h until TTL expires. The professional fix is `Purge Everything` or file-level purge at CDN dashboard.
2. **Not clean**: query-string pollution in HTML; breaks URL-based cache-busting patterns; makes source harder to read.
3. **Not reversible cleanly**: removing `v=2` doesn't guarantee fresh load; it just creates a new cache miss that then caches again.
4. **Not self-testing**: no automated test verifies the `v=2` parameter exists; if removed accidentally, stale loader returns silently.

---

## Professional Alternatives (Not Applied — User Chose Current Path)

1. **CDN Purge (professional)**: `curl -X POST` to Cloudflare purge API (`/zones/{zone}/purge_cache`) — clears edge instantly; no URL pollution.
2. **File rename (durable)**: rename loader file (e.g., `chart-loader-v2.js`) — guaranteed miss; clean revert by renaming back. Already demonstrated at `f8b473c` (reverted at `2293955`).
3. **Wait ~4h**: Cloudflare TTL expires naturally; rebuilt file served after expiry — no manual action needed.
4. **Cache-Control headers**: add `Cache-Control: no-cache` or `must-revalidate` in nginx config for `/static/js/` paths — professional server-level control.

---

## Verification

Before push: `grep "?v=2" gpu_monitor/templates/dashboard/rig_detail.html gpu_monitor/templates/base.html` → matches present.
After deploy + `systemctl restart nginx`: browser hard reload (`Ctrl+F5`) + CDN purge (if dashboard available) ensures `v=2` serves rebuilt loader. The loader content (`585` lines, `generateLabels` restored, `loadChartMultiGpu` present) matches working PR#185 (`dba1852`).

---

## Commit

Self-contained (`v=2` only in templates; no production logic changed). Independent revert (`git revert <sha>`). Branch: `feat/cdn-cache-bypass-v2`.

---

## Timestamp Approach (`?ts={% timezone.now|date:'U' %}`) — NOT IMPLEMENTED

More robust than `v=2`: URL changes every second → guaranteed CDN miss.
NOT professional (breaks CDN caching, increases origin load, untestable).
User confirmed: stick with `v=2`; document only. Not applied to code.
