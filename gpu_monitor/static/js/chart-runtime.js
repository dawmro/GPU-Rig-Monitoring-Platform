/* =====================================================================
 * chart-runtime.js — Chart loader orchestration and global state
 * =====================================================================
 *
 * Phase 0.4 JS extraction: holds the shared state (chartInstances,
 * current range/bucket, chartsLoaded) and the loadCharts() orchestrator
 * that was previously inline in rig_detail.html.
 *
 * loadCharts() runs a registry of 22 chart loaders with 100ms
 * staggering between them, to spread DB load. The registry must
 * match the HTML canvas order — see the comment there.
 *
 * Exposes (via window.GRM.ChartRuntime):
 *   - instances: dict of canvasId -> Chart instance
 *   - bucketMinutes: current bucket size in minutes
 *   - rangeHours: current range in hours
 *   - chartsLoaded: bool, set true after first load
 *   - loadCharts(rigUuid): the main entry point — loads all 22 charts
 *   - setChartRange(rangeHours, bucketMinutes): change the range and reload
 *   - labelForRange(rangeHours): returns the human-readable label
 *     ('24h', '7d', '30d') for a range in hours
 * ===================================================================== */

(function () {
    'use strict';

    var Loaders = window.GRM.ChartLoaders;

    var state = {
        instances: {},
        bucketMinutes: 1,
        rangeHours: 24,
        chartsLoaded: false,
    };

    // Map range (hours) to its display label.
    function labelForRange(rangeHours) {
        if (rangeHours === 24) return '24h';
        if (rangeHours === 168) return '7d';
        return '30d';
    }

    // Build the chart loaders array. Each entry is a function that
    // returns a Promise. Order MUST match the HTML canvas order in
    // rig_detail.html — see the comment there.
    function buildLoaders(uuid, range) {
        return window.GRM.ChartLoaders.buildFromRegistry(uuid, range);
    }


    // Main entry point: load all charts with 100ms staggering.
    // Without staggering, all 22 chart requests would hit the server
    // simultaneously, creating a burst of DB aggregation queries.
    // With 100ms delay between each, requests are spread over ~2.2s.
    function loadCharts(uuid) {
        var loaders = buildLoaders(uuid, state.rangeHours);
        // Simplified staggering: first immediate, rest with 100ms delay (same spread, cleaner)
        loaders.forEach(function (loader, i) {
            setTimeout(function () {
                loader().catch(function (e) {
                    console.error('Chart loader failed (index ' + i + '):', e);
                });
            }, i === 0 ? 0 : i * 100);
        });
        state.chartsLoaded = true;
    }

    // Change the range and reload. Updates button styles too.
    function setChartRange(rangeHours, bucketMinutes) {
        state.rangeHours = rangeHours;
        state.bucketMinutes = bucketMinutes;
        // Update button styles
        document.querySelectorAll('.chart-range-btn').forEach(function (btn) {
            btn.classList.remove('bg-blue-600', 'text-white');
            btn.classList.add('bg-gray-700', 'grm-text-gray');
        });
        // Highlight active button (24h / 7d / 30d id convention)
        var activeId = 'btn-range-' + labelForRange(rangeHours);
        var activeBtn = document.getElementById(activeId);
        if (activeBtn) {
            activeBtn.classList.remove('bg-gray-700', 'grm-text-gray');
            activeBtn.classList.add('bg-blue-600', 'text-white');
        }
        // Reload charts with new range
        var uuid = window.GRM.RigState.rigUuid;
        if (uuid) {
            loadCharts(uuid);
        }
        // Update chart heading labels
        var label = labelForRange(rangeHours);
        document.querySelectorAll('.chart-timeframe-label').forEach(function (el) {
            el.textContent = label;
        });
    }

    // Public API
    window.GRM = window.GRM || {};
    window.GRM.ChartRuntime = {
        // state exposed read-only-ish (caller can read but should not
        // mutate instances directly; use setChartRange etc.)
        get instances() { return state.instances; },
        get bucketMinutes() { return state.bucketMinutes; },
        get rangeHours() { return state.rangeHours; },
        get chartsLoaded() { return state.chartsLoaded; },
        set chartsLoaded(v) { state.chartsLoaded = v; },
        loadCharts: loadCharts,
        setChartRange: setChartRange,
        labelForRange: labelForRange,
    };
})();
