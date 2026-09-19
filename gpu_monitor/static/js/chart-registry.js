/* =====================================================================
 * chart-registry.js — Chart loader registry (Feature 5: self-contained)
 * =====================================================================
 * Separates chart definitions from loader functions. Runtime reads
 * this array; rig_detail.html no longer needs to match loader order
 * manually (though it still can).
 * ===================================================================== */
(function () {
    'use strict';

    var registry = [
        // GPU charts (8)
        { id: 'chartGpuTemp', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuTemp', 'gpu_temp_c', uuid, range, '°C'); } },
        { id: 'chartGpuFan', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuFan', 'gpu_fan_pct', uuid, range, '%'); } },
        { id: 'chartGpuUtil', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuUtil', 'gpu_util_pct', uuid, range, '%'); } },
        { id: 'chartGpuMemCtrlUtil', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuMemCtrlUtil', 'gpu_mem_controller_util_pct', uuid, range, '%'); } },
        { id: 'chartGpuPower', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuPower', 'gpu_power_w', uuid, range, 'W'); } },
        { id: 'chartGpuMem', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuMem', 'gpu_mem_used_mb', uuid, range, ' MB'); } },
        { id: 'chartGpuCoreClock', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuCoreClock', 'gpu_core_clock_mhz', uuid, range, ' MHz'); } },
        { id: 'chartGpuMemClock', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiGpu('chartGpuMemClock', 'gpu_mem_clock_mhz', uuid, range, ' MHz'); } },
        // CPU charts (5)
        { id: 'chartCpuUtil', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartCpuUtil', 'cpu_utilization_pct', uuid, range, '%', 'rgba(16, 185, 129, 0.8)', 'rgba(16, 185, 129, 0.15)'); } },
        { id: 'chartCpuTemp', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartCpuTemp', 'cpu_temp_c', uuid, range, '°C', 'rgba(245, 158, 11, 0.8)', 'rgba(245, 158, 11, 0.15)'); } },
        { id: 'chartCpuFreq', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartCpuFreq', 'cpu_freq_current_mhz', uuid, range, ' MHz', 'rgba(59, 130, 246, 0.8)', 'rgba(59, 130, 246, 0.15)'); } },
        { id: 'chartCpuPower', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartCpuPower', 'cpu_power_w', uuid, range, 'W', 'rgba(168, 85, 247, 0.8)', 'rgba(168, 85, 247, 0.15)'); } },
        { id: 'chartCpuLoad', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartLoadAvg('chartCpuLoad', uuid, range); } },
        // Disk charts (4)
        { id: 'chartDiskUsage', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiKey('chartDiskUsage', 'disk_usage_pct', uuid, range, '%', 'multi_disk'); } },
        { id: 'chartDiskReadWriteThroughput', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiKeyDual('chartDiskReadWriteThroughput', 'disk_read_bytes_delta', 'disk_write_bytes_delta', uuid, range, ' MB', 'multi_disk', 'rgba(59, 130, 246, 0.8)', 'rgba(16, 185, 129, 0.8)'); } },
        { id: 'chartDiskReadWriteIops', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiKeyDual('chartDiskReadWriteIops', 'disk_read_iops_delta', 'disk_write_iops_delta', uuid, range, ' IOPS', 'multi_disk', 'rgba(59, 130, 246, 0.8)', 'rgba(16, 185, 129, 0.8)'); } },
        { id: 'chartDiskUtilization', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMultiKey('chartDiskUtilization', 'disk_utilization_pct', uuid, range, '%', 'multi_disk'); } },
        // System / other (5)
        { id: 'chartTotalPower', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartTotalPower', 'total_system_power_w', uuid, range, 'W', 'rgba(59, 130, 246, 0.8)', 'rgba(59, 130, 246, 0.15)'); } },
        { id: 'chartMemSwap', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartMemSwap('chartMemSwap', uuid, range); } },
        { id: 'chartNetCombined', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChartNetworkCombined('chartNetCombined', uuid, range); } },
        { id: 'chartUptime', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartUptime', 'uptime_s', uuid, range, ' days', 'rgba(168, 85, 247, 0.8)', 'rgba(168, 85, 247, 0.15)'); } },
        { id: 'chartErrorFreq', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartErrorFreq', 'error_frequency', uuid, range, ' err/min', 'rgba(239, 68, 68, 0.8)', 'rgba(239, 68, 68, 0.5)', 'bar'); } },
        { id: 'chartActiveJob', loader: function (uuid, range) { return window.GRM.ChartLoaders.loadChart('chartActiveJob', 'has_active_job', uuid, range, '', 'rgba(255, 215, 0, 0.8)', 'rgba(255, 215, 0, 0.15)'); } },
    ];

    window.GRM = window.GRM || {};
    window.GRM.ChartLoaders = window.GRM.ChartLoaders || {};
    window.GRM.ChartLoaders.registry = registry;

    // Helper to build loader array from registry (matches chart-runtime buildLoaders contract)
    window.GRM.ChartLoaders.buildFromRegistry = function (uuid, range) {
        return registry.map(function (entry) {
            return function () { return entry.loader(uuid, range); };
        });
    };
})();
