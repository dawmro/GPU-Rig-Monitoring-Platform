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
        return [
            // GPU charts (8)
            function () { return Loaders.loadChartMultiGpu('chartGpuTemp',       'gpu_temp_c',                uuid, range, '°C'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuFan',        'gpu_fan_pct',               uuid, range, '%'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuUtil',       'gpu_util_pct',              uuid, range, '%'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuMemCtrlUtil','gpu_mem_controller_util_pct', uuid, range, '%'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuPower',      'gpu_power_w',               uuid, range, 'W'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuMem',        'gpu_mem_used_mb',           uuid, range, ' MB'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuCoreClock',  'gpu_core_clock_mhz',        uuid, range, ' MHz'); },
            function () { return Loaders.loadChartMultiGpu('chartGpuMemClock',   'gpu_mem_clock_mhz',         uuid, range, ' MHz'); },
            // CPU charts (5)
            function () { return Loaders.loadChart('chartCpuUtil',    'cpu_utilization_pct',  uuid, range, '%',    'rgba(16, 185, 129, 0.8)', 'rgba(16, 185, 129, 0.15)'); },
            function () { return Loaders.loadChart('chartCpuTemp',    'cpu_temp_c',           uuid, range, '°C',   'rgba(245, 158, 11, 0.8)', 'rgba(245, 158, 11, 0.15)'); },
            function () { return Loaders.loadChart('chartCpuFreq',    'cpu_freq_current_mhz', uuid, range, ' MHz', 'rgba(59, 130, 246, 0.8)', 'rgba(59, 130, 246, 0.15)'); },
            function () { return Loaders.loadChart('chartCpuPower',   'cpu_power_w',          uuid, range, 'W',    'rgba(168, 85, 247, 0.8)', 'rgba(168, 85, 247, 0.15)'); },
            function () { return Loaders.loadChartLoadAvg('chartCpuLoad', uuid, range); },
            // Disk charts (4)
            function () { return Loaders.loadChartMultiKey('chartDiskUsage',            'disk_usage_pct',      uuid, range, '%',   'multi_disk'); },
            function () { return Loaders.loadChartMultiKeyDual('chartDiskReadWriteThroughput', 'disk_read_bytes_delta', 'disk_write_bytes_delta', uuid, range, ' MB', 'multi_disk', 'rgba(59, 130, 246, 0.8)', 'rgba(16, 185, 129, 0.8)'); },
            function () { return Loaders.loadChartMultiKeyDual('chartDiskReadWriteIops',     'disk_read_iops_delta',  'disk_write_iops_delta',  uuid, range, ' IOPS', 'multi_disk', 'rgba(59, 130, 246, 0.8)', 'rgba(16, 185, 129, 0.8)'); },
            function () { return Loaders.loadChartMultiKey('chartDiskUtilization',     'disk_utilization_pct', uuid, range, '%',   'multi_disk'); },
            // System / other (5)
            function () { return Loaders.loadChart('chartTotalPower', 'total_system_power_w', uuid, range, 'W',  'rgba(59, 130, 246, 0.8)', 'rgba(59, 130, 246, 0.15)'); },
            function () { return Loaders.loadChartMemSwap('chartMemSwap', uuid, range); },
            function () { return Loaders.loadChartNetworkCombined('chartNetCombined', uuid, range); },
            function () { return Loaders.loadChart('chartUptime',     'uptime_s',         uuid, range, ' days',  'rgba(168, 85, 247, 0.8)', 'rgba(168, 85, 247, 0.15)'); },
            function () { return Loaders.loadChart('chartErrorFreq',   'error_frequency',  uuid, range, ' err/min', 'rgba(239, 68, 68, 0.8)', 'rgba(239, 68, 68, 0.5)', 'bar'); },
            // Job status: line chart like error_frequency, bool aggregated with MAX (any True = 1, else 0)
            function () { return Loaders.loadChart('chartActiveJob',  'has_active_job',  uuid, range, 'Active %', 'rgba(255, 215, 0, 0.8)', 'rgba(255, 215, 0, 0.15)'); },
        ];
    }

    // Helper: load a chart after a delay (Promise-based).
    function delayedLoad(fn, delay) {
        return new Promise(function (resolve) {
            setTimeout(function () { fn().then(resolve); }, delay);
        });
    }

    // Main entry point: load all charts with 100ms staggering.
    // Without staggering, all 22 chart requests would hit the server
    // simultaneously, creating a burst of DB aggregation queries.
    // With 100ms delay between each, requests are spread over ~2.2s.
    function loadCharts(uuid) {
        var loaders = buildLoaders(uuid, state.rangeHours);
        // Run sequentially with first chart immediate, then staggered
        var p = Promise.resolve();
        loaders.forEach(function (loader, i) {
            p = p.then(function () {
                return i === 0 ? loader() : delayedLoad(loader, 100);
            });
        });
        return p;
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
