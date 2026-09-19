# JS File Import References — Full Inventory
Branch: `cleanup/list-js-imports` (from pulled `main`).

---

## Template Imports (`gpu_monitor/templates/`)

### `base.html`
- Line 12: `<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>` — Chart.js CDN (external, not project file)
- Line 75: `<script src="{% static 'js/app-base.js' %}"></script>` — `app-base.js`

### `dashboard/rig_detail.html`
- Line 310: `{% static 'js/chart-base.js' %}` — `chart-base.js`
- Line 311: `{% static 'js/chart-colors.js' %}` — `chart-colors.js`
- Line 312: `{% static 'js/chart-loaders.js' %}` — loader (restored to working PR#185 version)
- Line 313: `{% static 'js/chart-registry.js' %}` — registry (Feature 5, `refactor/chart-routes-js`)
- Line 314: `{% static 'js/chart-runtime.js' %}` — runtime (simplified staggering at `b2a1375`)
- Line 315: `{% static 'js/rig-detail.js' %}` — `rig-detail.js`

---

## Source Files (`gpu_monitor/static/js/`)

All are project-owned (not CDN):
- `app-base.js` (139 lines) — clock, mobile menu, email toggle
- `chart-base.js` (272 lines) — shared options, tooltip, legend (includes `generateLabels`)
- `chart-colors.js` — color palette (not inspected)
- `chart-loaders.js` (585 lines) — 7 loader functions (restored from PR#185)
- `chart-registry.js` (new, Feature 5) — registry array + `buildFromRegistry`
- `chart-runtime.js` (144 lines) — orchestration, `loadCharts()` with `setTimeout`
- `rig-detail.js` — page-specific logic (not inspected for bloat)

---

## Professional Notes
- `chart-loaders.js` is the working loader (`d32fb1a` restore); `chart-loader-v2.js` removed at `75b333e` (stale file clean-up).
- Registry (`chart-registry.js`) separates definitions from runtime — Feature 5, self-contained (`a0837f4`).
- No `.py` files in any JS directory; no stray docs (`references/` removed, `PLAN-chart-revert.md` removed, `README.md` removed from `static/js/`).
- All references verified against `docs/professional-plan-chart-refactor.md` (6-step plan).
