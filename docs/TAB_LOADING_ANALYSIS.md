# Tab Loading Strategy Analysis

## Current Behavior

### Page Load Flow
1. **Page loads** → Live Metrics tab is visible by default
2. **DOMContentLoaded** (line 482-487) fires:
   - `loadCharts()` is called IMMEDIATELY
   - `chartsLoaded = true` set
3. **Charts tab** opens ~1.7s later (100ms stagger × 18 charts)
4. **Other tabs** (Containers, Errors, Report) load on-demand

### What Loads on Live Metrics Tab (Default)
- **Live metrics cards**: HTMX every 30s (`hx-trigger="every 30s"`)
- **Latest snapshot data** only - no historical queries

### What Loads Immediately on Page Load (PROBLEM)
- **21 chart API calls** (one per chart) via `loadCharts()`
- Each chart: 18× `fetch()` calls to `/api/v1/rigs/<uuid>/chart-data/`
- Total: ~18 API endpoints queried on every page load
- Staggered by 100ms × 21 → ~2.1 seconds of backend load

## Tab Structure
4 tabs besides Live Metrics:
| Tab | Content | Loading Strategy |
|-----|---------|------------------|
| Live Metrics (default) | Real-time metrics cards | HTMX auto-refresh |
| Historical Charts | 21 chart.js graphs | **EAGER** (immediate on page load) ❌ |
| Containers | Container status | HTMX lazy (on-click) |
| Errors | Error list | HTMX lazy (on-click) |
| Report | Report table | **LAZY** (on first open) ✓ |

## Proposed Fix: Lazy Chart Loading

### Change Required
Move chart loading from `DOMContentLoaded` to tab switch trigger.

**Before (line 481-487):**
```javascript
document.addEventListener('DOMContentLoaded', function() {
    if (!chartsLoaded) {
        loadCharts();
        chartsLoaded = true;
    }
});
```

**After:**
```javascript
// Remove the above entirely - charts load on tab switch
// Keep the existing switchTab() lazy-loading logic:
if (tabName === 'charts' && !chartsLoaded) {
    chartsLoaded = true;
    loadCharts();
}
```

### Benefits
- User lands on Live Metrics → only metrics cards load
- Chart API queries deferred until Charts tab opened
- Reduces backend load by ~18 queries per page view
- ~2.1 seconds of DB aggregation avoided for users who don't view charts

### Edge Cases
| Case | Handling |
|------|----------|
| User never opens Charts tab | No chart queries fired (good) |
| User opens Charts tab immediately | Charts load on tab switch (same behavior) |
| User opens Charts, then switches away | Charts stay in memory, no reload needed |
| Report tab already lazy-loads | Same pattern, proven to work |
| Timeframe buttons clicked before chart load | Range state propagates correctly |

## Additional Optimization: Remove Redundant Lazy Check

Current code has lazy check in switchTab() (line 375-378):
```javascript
if (tabName === 'charts' && !chartsLoaded) {
    chartsLoaded = true;
    loadCharts();
}
```

If we remove DOMContentLoaded loading, this check already handles lazy loading.