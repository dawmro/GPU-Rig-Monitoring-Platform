/* =====================================================================
 * chart-base.js — Shared Chart.js options and utilities
 * =====================================================================
 *
 * Phase 0.4 JS extraction: this file holds the chart-axis configuration
 * that was previously duplicated 6 times in rig_detail.html. Every
 * line chart in the rig detail page used the same scales.x / scales.y /
 * interaction block; the only differences were the tooltip callback
 * (single vs multi dataset) and the y-axis title (unit text).
 *
 * Used by:
 *   - chart-loaders.js (loadChart, loadChartLoadAvg, loadChartMemSwap,
 *     loadChartMultiGpu, loadChartMultiKey, loadChartMultiKeyDual)
 *
 * Depends on:
 *   - Chart.js (loaded via CDN in base.html)
 *
 * Exposes (via window.GRM.ChartBase):
 *   - baseOptions(opts): returns a complete options object with the
 *     shared x/y/interaction blocks merged with caller-provided opts
 *   - xAxisOptions(): the scales.x block
 *   - yAxisOptions(unit, opts): the scales.y block (unit becomes title)
 *   - interactionOptions(): the interaction block
 *   - tooltipSingle(unit): single-dataset tooltip formatter
 *   - tooltipMulti(formatFn): multi-dataset tooltip formatter
 *   - noDataMessage(): returns the HTML to show when chart has no data
 *   - safeDestroy(canvasId, instances): destroy a chart instance if it
 *     exists (so re-renders don't stack)
 *
 * Style constants (extracted from the inline chart options) are also
 * here so they can be tweaked in one place.
 * ===================================================================== */

(function () {
    'use strict';

    // Style constants — these were inlined 6+ times in the original
    // rig_detail.html. Centralized so they can be tweaked once.
    var STYLE = {
        gridColor: 'rgba(255,255,255,0.05)',
        tickColor: '#9ca3af',
        axisTitleColor: '#6b7280',
        axisTitleFontSize: 11,
        maxTicksLimit: 12,
        pointRadius: 0,
        pointHitRadius: 10,
        borderWidth: 2,
        barBorderWidth: 0,
        tension: 0.0,
        lineSpanGaps: false,
        maxLabelLength: 12,
    };

    // Common x-axis configuration: grid + ticks + dynamic label skipping.
    // Used by all 6 chart loaders.
    function xAxisOptions() {
        return {
            grid: { color: STYLE.gridColor },
            ticks: {
                color: STYLE.tickColor,
                maxTicksLimit: STYLE.maxTicksLimit,
                maxRotation: 0,
                autoSkip: true,
                // Show ~12 labels evenly across all buckets.
                callback: function (val, index) {
                    var total = this.getLabels().length;
                    var skip = Math.max(1, Math.floor(total / 12));
                    return index % skip === 0 ? this.getLabelForValue(val) : '';
                },
            },
        };
    }

    // Common y-axis configuration. `unit` becomes the axis title;
    // `opts.beginAtZero` defaults to true (the original behavior was
    // always beginAtZero except for the network chart, which also
    // wanted beginAtZero).
    function yAxisOptions(unit, opts) {
        opts = opts || {};
        var config = {
            grid: { color: STYLE.gridColor },
            ticks: { color: STYLE.tickColor },
            beginAtZero: opts.beginAtZero !== false,
            title: {
                display: (unit !== ''),
                text: unit || 'Job / No Job',
                color: STYLE.axisTitleColor,
                font: { size: STYLE.axisTitleFontSize },
            },
        };
        // For charts with a secondary y-axis (network combined)
        if (opts.position) {
            config.position = opts.position;
            config.type = opts.type || 'linear';
        }
        if (opts.drawOnChartArea === false) {
            config.grid = Object.assign({}, config.grid, { drawOnChartArea: false });
        }
        if (opts.tickColor) {
            config.ticks = Object.assign({}, config.ticks, { color: opts.tickColor });
            config.title = Object.assign({}, config.title, { color: opts.tickColor });
        }
        return config;
    }

    // Common interaction config: tooltip on x-axis hover, anywhere on the column.
    function interactionOptions() {
        return {
            mode: 'nearest',
            axis: 'x',
            intersect: false,
        };
    }

    // Common legend config: visible legend with dark theme colors.
    // `opts` may include:
    //   - padding: int (default 8) — space between legend items
    //   - fontSize: int (default 11) — legend label font size
    //   - generateLabels: function(chart) — optional custom label
    //     processor (used by NetworkCombined for truncating IPs, and by
    //     MultiGpu for truncating GPU UUIDs).
    function legendOptions(opts) {
        opts = opts || {};
        var labels = {
            color: STYLE.tickColor,
            boxWidth: 12,
            padding: opts.padding || 8,
            font: { size: opts.fontSize || 11 },
        };
        if (opts.generateLabels) {
            labels.generateLabels = opts.generateLabels;
        }
        return {
            display: true,
            labels: labels,
        };
    }

    // Build a chart-data API URL for the given uuid/range. Optional
    // `params.metric` adds "?metric=..."; optional `params.extra` adds
    // arbitrary key=value pairs. Centralized so the bucketMinutes and
    // route format are kept consistent across all 7 loaders.
    function buildChartUrl(uuid, range, params) {
        params = params || {};
        var url = '/api/v1/rigs/' + uuid + '/chart-data/?range=' + range +
                  '&bucket_minutes=' + window.GRM.ChartRuntime.bucketMinutes;
        if (params.metric) {
            url += '&metric=' + params.metric;
        }
        if (params.extra) {
            Object.keys(params.extra).forEach(function (k) {
                url += '&' + k + '=' + params.extra[k];
            });
        }
        return url;
    }

    // Tooltip formatter for single-dataset charts.
    // Shows: "<unit><value>" (e.g. "°C75")
    function tooltipSingle(unit) {
        return {
            mode: 'index',
            intersect: false,
            callbacks: {
                label: function (ctx) {
                    return ctx.parsed.y + unit;
                },
            },
        };
    }

    // Tooltip formatter for job status (bool 0/1): shows "Job" for 1, "No Job" for 0
    function tooltipJob() {
        return {
            mode: 'index',
            intersect: false,
            callbacks: {
                label: function (ctx) {
                    return ctx.parsed.y === 1 ? 'Job' : 'No Job';
                },
            },
        };
    }

    // Tooltip formatter for multi-dataset charts.
    // `formatFn` is called with (parsed_y, dataset_label) and returns
    // the display string. If formatFn is omitted, defaults to
    // "<label>: <y.toFixed(2)>".
    function tooltipMulti(formatFn) {
        var fn = formatFn || function (y, label) {
            return label + ': ' + (y !== null ? y.toFixed(2) : '—');
        };
        return {
            mode: 'index',
            intersect: false,
            callbacks: {
                label: function (ctx) {
                    return fn(ctx.parsed.y, ctx.dataset.label);
                },
            },
        };
    }

    // "No data" HTML shown when a chart returns an empty dataset.
    // Centralized so it stays consistent across all chart types.
    function noDataMessage() {
        return '<div class="flex items-center justify-center h-48 text-gray-500 text-sm">No data available</div>';
    }

    // Destroy an existing chart instance (if any) by canvasId.
    // The caller is responsible for maintaining the instances dict.
    function safeDestroy(canvasId, instances) {
        if (instances[canvasId]) {
            instances[canvasId].destroy();
        }
    }

    // Build a complete options object from a partial set of overrides.
    // `opts` should include `tooltip`, `y` (unit), and any chart-specific
    // overrides. The shared x/y/interaction blocks are merged in.
    function baseOptions(opts) {
        opts = opts || {};
        return {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: opts.legend || { display: false },
                tooltip: opts.tooltip || tooltipSingle(''),
            },
            scales: {
                x: opts.x || xAxisOptions(),
                y: opts.y || yAxisOptions(opts.unit || ''),
            },
            interaction: opts.interaction || interactionOptions(),
        };
    }

    // Standard single-dataset style for line charts.
    function lineDataset(label, data, borderColor, bgColor, chartType) {
        chartType = chartType || 'line';
        return {
            label: label,
            data: data,
            borderColor: borderColor,
            backgroundColor: bgColor,
            borderWidth: chartType === 'bar' ? STYLE.barBorderWidth : STYLE.borderWidth,
            fill: chartType === 'line',
            tension: STYLE.tension,
            pointRadius: STYLE.pointRadius,
            pointHitRadius: STYLE.pointHitRadius,
            spanGaps: STYLE.lineSpanGaps,
        };
    }

    // Public API
    window.GRM = window.GRM || {};
    window.GRM.ChartBase = {
        STYLE: STYLE,
        xAxisOptions: xAxisOptions,
        yAxisOptions: yAxisOptions,
        interactionOptions: interactionOptions,
        legendOptions: legendOptions,
        buildChartUrl: buildChartUrl,
        tooltipSingle: tooltipSingle,
        tooltipJob: tooltipJob,
        tooltipMulti: tooltipMulti,
        noDataMessage: noDataMessage,
        safeDestroy: safeDestroy,
        baseOptions: baseOptions,
        lineDataset: lineDataset,
    };
})();
