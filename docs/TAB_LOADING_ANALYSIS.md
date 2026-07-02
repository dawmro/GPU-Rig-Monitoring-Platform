# Tab Loading Strategy Analysis

## Implemented Behavior (After Fix)

### Page Load Flow
1. **Page loads** → Live Metrics tab is visible by default
2. **No chart loading** from DOMContentLoaded
3. **Charts load** only when user switches to Charts tab via `switchTab()` (line 363-377)
4. **Other tabs** (Containers, Errors, Report) load on-demand via HTMX

### What Loads on Live Metrics Tab (Default)
- **Live metrics cards**: HTMX every 30s (`hx-trigger="every 30s"`)
- **Latest snapshot data** only - no historical queries
- **Zero chart API calls** - deferred until Charts tab opened

### What Previously Loaded on Page Load (WAS THE PROBLEM)
- **21 chart API calls** (one per chart) via `loadCharts()` - now deferred
- Each chart: ~2 fetch() calls to `/api/v1/rigs/<uuid>/chart-data/`
- Total: ~18 API endpoints queried on every page load - now avoided
- Staggered by 100ms × 21 → ~2.1 seconds of backend load - now saved

## Tab Structure
5 tabs total:

| Tab | Content | Loading Strategy |
|-----|---------|------------------|
| Live Metrics (default) | Real-time metrics cards | HTMX auto-refresh (every 30s) |
| Historical Charts | 21 chart.js graphs | **LAZY** (on tab switch) ✓ |
| Containers | Container status | HTMX lazy (on-click) |
| Errors | Error list | HTMX lazy (on-click) |
| Report | Report table | **LAZY** (on first open) ✓ |

## Implementation Change

**Removed from rig_detail.html (line 481-487):**
```javascript
// Load charts on page load
document.addEventListener('DOMContentLoaded', function() {
    if (!chartsLoaded) {
        loadCharts();
        chartsLoaded = true;
    }
});
```

**Lazy loading preserved in `switchTab()` function:**
```javascript
if (tabName === 'charts' && !chartsLoaded) {
    chartsLoaded = true;
    loadCharts();
}
```

## Benefits
- User lands on Live Metrics tab - only metrics cards load
- Chart API queries deferred until Charts tab opened
- Reduces backend load by ~18 queries per page view
- ~2.1 seconds of DB aggregation avoided for users who don't view charts

## Edge Cases Handled
| Case | Handling |
|------|----------|
| User never opens Charts tab | No chart queries fired (good) |
| User opens Charts tab immediately | Charts load on tab switch (same behavior) |
| User opens Charts, then switches away | Charts stay in memory, no reload needed |
| Report tab already lazy-loads | Same pattern, proven to work |
| Timeframe buttons clicked before chart load | Range state propagates correctly to `loadCharts()` |