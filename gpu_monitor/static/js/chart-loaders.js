/* =====================================================================
 * chart-loaders.js — Chart.js data loaders
 * =====================================================================
 *
 * Phase 0.4 JS extraction: this file consolidates the 6 chart loader
 * functions that were previously inline in rig_detail.html:
 *
 *   - loadChart              : single-dataset, single metric
 *   - loadChartLoadAvg       : 3-series (1m/5m/15m)
 *   - loadChartMemSwap       : 3-series (used/free/swap) with fill
 *   - loadChartNetworkCombined : 3 fetches (RX/TX/Errors) with dual axis
 *   - loadChartMultiKey      : N-series from multi_disk / multi_iface
 *   - loadChartMultiKeyDual  : 2 fetches, N-series (read/write)
 *   - loadChartMultiGpu      : N-series from multi_gpu
 *
 * The data is fetched from /api/v1/rigs/{uuid}/chart-data/ and rendered
 * via Chart.js. State (uuid, range, bucketMinutes) is read from
 * window.GRM.ChartRuntime (see chart-runtime.js).
 *
 * Exposes (via window.GRM.ChartLoaders):
 *   - loadChart, loadChartLoadAvg, loadChartMemSwap,
 *   - loadChartNetworkCombined, loadChartMultiKey, loadChartMultiKeyDual,
 *   - loadChartMultiGpu
 *   - fetchChartData(url): shared fetch helper with error handling
 * ===================================================================== */

(function () {
    'use strict';

    var Base = window.GRM.ChartBase;
    var Colors = window.GRM.ChartColors;

    // Shared fetch helper. Returns parsed JSON on success, or null
    // on any error (network, HTTP, parse). On no-data, sets
    // ctx.parentElement.innerHTML to the standard "No data" message.
    function fetchChartData(url) {
        return fetch(url).then(function (resp) {
            if (!resp.ok) {
                throw new Error('HTTP ' + resp.status);
            }
            return resp.json();
        });
    }

    // Convert a metric name (snake_case) to a display label.
    // "cpu_utilization_pct" -> "Cpu Utilization Pct"
    // The original loadChart did this inline; consolidated here.
    function metricToLabel(metric) {
        return metric.replace(/_/g, ' ').replace(/\b\w/g, function (c) {
            return c.toUpperCase();
        });
    }

    // ---------------------------------------------------------------
    // loadChart — single-dataset, single metric
    // ---------------------------------------------------------------
    // The most common chart type: one line, one color, fetched once.
    // Used for CPU utilization, CPU temp, CPU freq, CPU power, total
    // system power, uptime, error frequency.
    function loadChart(canvasId, metric, uuid, range, unit, borderColor, bgColor, chartType) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();
        chartType = chartType || 'line';

        var url = Base.buildChartUrl(uuid, range, { metric: metric });

        return fetchChartData(url).then(function (data) {
            if (!data || !data.datasets || !data.datasets[0] || !data.datasets[0].data) {
                throw new Error('Invalid data format');
            }
            Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

            window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                type: chartType,
                data: {
                    labels: data.labels,
                    datasets: [Base.lineDataset(
                        metricToLabel(metric),
                        data.datasets[0].data,
                        borderColor,
                        bgColor,
                        chartType
                    )],
                },
                options: Base.baseOptions({
                    unit: unit,
                    tooltip: Base.tooltipSingle(unit),
                }),
            });
        }).catch(function (e) {
            console.error('Failed to load chart ' + metric + ':', e);
            ctx.parentElement.innerHTML = Base.noDataMessage();
        });
    }

    // ---------------------------------------------------------------
    // loadChartLoadAvg — 3-series (1m, 5m, 15m)
    // ---------------------------------------------------------------
    function loadChartLoadAvg(canvasId, uuid, range) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();

        var url = Base.buildChartUrl(uuid, range, { metric: 'cpu_load_avg' });

        return fetchChartData(url).then(function (data) {
            Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

            window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: data.datasets.map(function (ds, i) {
                        var color = Colors.colorAt(Colors.LOADAVG, i);
                        return {
                            label: ds.label,
                            data: ds.data,
                            borderColor: color.border,
                            backgroundColor: color.bg,
                            borderWidth: Base.STYLE.borderWidth,
                            fill: false,
                            tension: Base.STYLE.tension,
                            pointRadius: Base.STYLE.pointRadius,
                            pointHitRadius: Base.STYLE.pointHitRadius,
                            spanGaps: Base.STYLE.lineSpanGaps,
                        };
                    }),
                },
                options: Base.baseOptions({
                    unit: 'Load',
                    legend: Base.legendOptions(),
                    tooltip: Base.tooltipMulti(function (y, label) {
                        return label + ': ' + (y !== null ? y.toFixed(2) : '—');
                    }),
                }),
            });
        }).catch(function (e) {
            console.error('Failed to load chart cpu_load_avg:', e);
            ctx.parentElement.innerHTML = Base.noDataMessage();
        });
    }

    // ---------------------------------------------------------------
    // loadChartMemSwap — 3-series (used/free/swap) with fill
    // ---------------------------------------------------------------
    // First series (memory used) is rendered as a filled area; the
    // others as plain lines. The fill signals "this is what you're
    // using; everything else is overhead".
    function loadChartMemSwap(canvasId, uuid, range) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();

        var url = Base.buildChartUrl(uuid, range, { metric: 'mem_used_bytes', extra: { multi_mem: 'true' } });

        return fetchChartData(url).then(function (data) {
            if (!data || !data.datasets || data.datasets.length === 0) {
                ctx.parentElement.innerHTML = Base.noDataMessage();
                return;
            }
            Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

            window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: data.datasets.map(function (ds, i) {
                        var color = Colors.colorAt(Colors.MEM, i);
                        return {
                            label: ds.label,
                            data: ds.data,
                            borderColor: color.border,
                            backgroundColor: color.bg,
                            borderWidth: Base.STYLE.borderWidth,
                            fill: i === 0,  // only fill the first dataset (used)
                            tension: Base.STYLE.tension,
                            pointRadius: Base.STYLE.pointRadius,
                            pointHitRadius: Base.STYLE.pointHitRadius,
                            spanGaps: Base.STYLE.lineSpanGaps,
                        };
                    }),
                },
                options: Base.baseOptions({
                    unit: 'GB',
                    legend: Base.legendOptions(),
                    tooltip: Base.tooltipMulti(function (y, label) {
                        return label + ': ' + (y !== null ? y.toFixed(2) + ' GB' : '—');
                    }),
                }),
            });
        }).catch(function (e) {
            console.error('Failed to load memory chart:', e);
            ctx.parentElement.innerHTML = Base.noDataMessage();
        });
    }

    // ---------------------------------------------------------------
    // loadChartNetworkCombined — RX + TX (left axis) + Errors (right axis)
    // ---------------------------------------------------------------
    // The only chart in the project with a dual Y-axis. Errors use
    // a different color (red) and a different scale (count, not bytes).
    function loadChartNetworkCombined(canvasId, uuid, range) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();

        var ifaceExtra = { multi_iface: 'true' };
        var rxUrl = Base.buildChartUrl(uuid, range, { metric: 'net_rx_bytes_delta', extra: ifaceExtra });
        var txUrl = Base.buildChartUrl(uuid, range, { metric: 'net_tx_bytes_delta', extra: ifaceExtra });
        var errUrl = Base.buildChartUrl(uuid, range, { metric: 'net_rx_errors', extra: ifaceExtra });

        return Promise.all([fetch(rxUrl), fetch(txUrl), fetch(errUrl)])
            .then(function (responses) {
                return Promise.all([
                    responses[0].json(),
                    responses[1].json(),
                    responses[2].json(),
                ]);
            })
            .then(function (data) {
                var rxData = data[0], txData = data[1], errData = data[2];
                var labels = rxData.labels;
                Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

                var datasets = [];
                // RX datasets — solid lines, left axis
                rxData.datasets.forEach(function (ds) {
                    var color = Colors.NETWORK[0];
                    datasets.push({
                        label: ds.label + ' RX',
                        data: ds.data,
                        borderColor: color.border,
                        backgroundColor: color.bg,
                        borderWidth: Base.STYLE.borderWidth,
                        fill: false,
                        tension: Base.STYLE.tension,
                        pointRadius: Base.STYLE.pointRadius,
                        pointHitRadius: Base.STYLE.pointHitRadius,
                        spanGaps: Base.STYLE.lineSpanGaps,
                        yAxisID: 'y',
                        borderDash: [],
                    });
                });
                // TX datasets — dashed lines, left axis
                txData.datasets.forEach(function (ds) {
                    var color = Colors.NETWORK[1];
                    datasets.push({
                        label: ds.label + ' TX',
                        data: ds.data,
                        borderColor: color.border,
                        backgroundColor: color.bg,
                        borderWidth: Base.STYLE.borderWidth,
                        fill: false,
                        tension: Base.STYLE.tension,
                        pointRadius: Base.STYLE.pointRadius,
                        pointHitRadius: Base.STYLE.pointHitRadius,
                        spanGaps: Base.STYLE.lineSpanGaps,
                        yAxisID: 'y',
                        borderDash: [6, 3],
                    });
                });
                // Errors datasets — bars, right axis
                errData.datasets.forEach(function (ds) {
                    var color = Colors.NETWORK[2];
                    datasets.push({
                        label: ds.label + ' Err',
                        data: ds.data,
                        borderColor: color.border,
                        backgroundColor: color.bg,
                        borderWidth: Base.STYLE.barBorderWidth,
                        fill: false,
                        tension: Base.STYLE.tension,
                        pointRadius: Base.STYLE.pointRadius,
                        pointHitRadius: Base.STYLE.pointHitRadius,
                        spanGaps: Base.STYLE.lineSpanGaps,
                        yAxisID: 'y1',
                        type: 'bar',
                        barThickness: 2,
                        barPercentage: 0.9,
                        categoryPercentage: 0.8,
                    });
                });

                window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                    type: 'line',
                    data: { labels: labels, datasets: datasets },
                    options: Base.baseOptions({
                        unit: 'MB',
                        legend: Base.legendOptions({
                            padding: 6,
                            fontSize: 10,
                            // Shorten interface labels: "ens33 192.168.1.10 RX"
                            // → "ens33 … RX" (truncate the IP part)
                            generateLabels: function (chart) {
                                var original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                                return original.map(function (label) {
                                    if (!label.text) return label;
                                    var spaceIdx = label.text.indexOf(' ');
                                    if (spaceIdx <= 0) return label;
                                    var ifacePart = label.text.substring(0, spaceIdx);
                                    var dirPart = label.text.substring(spaceIdx + 1);
                                    var parts = ifacePart.split(' ');
                                    if (parts[0].length > 8) {
                                        label.text = parts[0].substring(0, 8) + '…' +
                                                     (parts[1] ? ' ' + parts[1] : '') + ' ' + dirPart;
                                    }
                                    return label;
                                });
                            },
                        }),
                        tooltip: {
                            mode: 'index',
                            intersect: false,
                            callbacks: {
                                label: function (ctx) {
                                    var val = ctx.parsed.y;
                                    if (val === null) return ctx.dataset.label + ': —';
                                    if (ctx.dataset.yAxisID === 'y1') {
                                        return ctx.dataset.label + ': ' + val + ' err';
                                    }
                                    return ctx.dataset.label + ': ' + val.toFixed(2) + ' MB';
                                },
                            },
                        },
                        // Dual y-axis: y (left) and y1 (right)
                        x: Base.xAxisOptions(),
                        y: Base.yAxisOptions('MB', { position: 'left' }),
                    }),
                });
                // Inject the second y-axis after baseOptions is applied
                // (baseOptions only sets y, so we add y1 here)
                window.GRM.ChartRuntime.instances[canvasId].options.scales.y1 =
                    Base.yAxisOptions('Errors', {
                        position: 'right',
                        drawOnChartArea: false,
                        tickColor: '#f87171',
                    });
            })
            .catch(function (e) {
                console.error('Failed to load network chart:', e);
                ctx.parentElement.innerHTML = Base.noDataMessage();
            });
    }

    // ---------------------------------------------------------------
    // loadChartMultiKey — N-series from multi_disk / multi_iface
    // ---------------------------------------------------------------
    // Used for disk usage and disk utilization (multi_disk), and could
    // be used for multi_iface. The number of series is not known
    // ahead of time, so we use the GPU_COLORS palette and cycle through.
    function loadChartMultiKey(canvasId, metric, uuid, range, unit, multiParam) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();

        var extra = {};
        extra[multiParam] = 'true';
        var url = Base.buildChartUrl(uuid, range, { metric: metric, extra: extra });

        return fetchChartData(url).then(function (data) {
            if (!data || !data.datasets || data.datasets.length === 0) {
                ctx.parentElement.innerHTML = Base.noDataMessage();
                return;
            }
            Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

            window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: data.datasets.map(function (ds, i) {
                        var color = Colors.colorAt(Colors.GPU_COLORS, i);
                        return {
                            label: ds.label,
                            data: ds.data,
                            borderColor: color.border,
                            backgroundColor: color.bg,
                            borderWidth: Base.STYLE.borderWidth,
                            fill: false,
                            tension: Base.STYLE.tension,
                            pointRadius: Base.STYLE.pointRadius,
                            pointHitRadius: Base.STYLE.pointHitRadius,
                            spanGaps: Base.STYLE.lineSpanGaps,
                        };
                    }),
                },
                options: Base.baseOptions({
                    unit: unit,
                    legend: Base.legendOptions({
                        fontSize: 10,
                        // Truncate long label (e.g. "sda /boot") to
                        // first 16 chars + ellipsis
                        generateLabels: function (chart) {
                                var original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                                return original.map(function (label) {
                                    if (!label.text) return label;
                                    var spaceIdx = label.text.indexOf(' ');
                                    if (spaceIdx > 0) {
                                        var keyPart = label.text.substring(0, spaceIdx);
                                        var extraPart = label.text.substring(spaceIdx + 1);
                                        if (keyPart.length > 16) {
                                            label.text = keyPart.substring(0, 16) + '… ' + extraPart;
                                        }
                                    } else if (label.text.length > 20) {
                                        label.text = label.text.substring(0, 20) + '…';
                                    }
                                    return label;
                                });
                        }
                    }),
                    tooltip: Base.tooltipMulti(function (y, label) {
                        return label + ': ' + (y !== null ? y.toFixed(1) + unit : '—');
                    }),
                }),
            });
        }).catch(function (e) {
            console.error('Failed to load multi-key chart ' + metric + ':', e);
            ctx.parentElement.innerHTML = Base.noDataMessage();
        });
    }

    // ---------------------------------------------------------------
    // loadChartMultiKeyDual — 2 fetches (read/write), N-series each
    // ---------------------------------------------------------------
    // Used for disk read/write throughput and IOPS. Fetches two
    // metrics and renders them as two sets of series on the same chart.
    // Read series are dashed; write series are solid.
    function loadChartMultiKeyDual(canvasId, metricRead, metricWrite, uuid, range, unit, multiParam, colorRead, colorWrite) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();

        var extra = {};
        extra[multiParam] = 'true';
        var readUrl = Base.buildChartUrl(uuid, range, { metric: metricRead, extra: extra });
        var writeUrl = Base.buildChartUrl(uuid, range, { metric: metricWrite, extra: extra });

        return Promise.all([fetchChartData(readUrl), fetchChartData(writeUrl)])
            .then(function (results) {
                var dataRead = results[0];
                var dataWrite = results[1];
                if (!dataRead.datasets || !dataWrite.datasets || dataRead.datasets.length === 0) {
                    ctx.parentElement.innerHTML = Base.noDataMessage();
                    return;
                }
                Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

                var datasets = [];
                var labels = dataRead.labels;
                dataRead.datasets.forEach(function (ds) {
                    datasets.push({
                        label: ds.label + ' Read',
                        data: ds.data,
                        borderColor: colorRead,
                        backgroundColor: colorRead.replace('0.8', '0.15'),
                        borderWidth: Base.STYLE.borderWidth,
                        borderDash: [4, 2],
                        fill: false,
                        tension: Base.STYLE.tension,
                        pointRadius: Base.STYLE.pointRadius,
                        pointHitRadius: Base.STYLE.pointHitRadius,
                        spanGaps: Base.STYLE.lineSpanGaps,
                    });
                });
                dataWrite.datasets.forEach(function (ds) {
                    datasets.push({
                        label: ds.label + ' Write',
                        data: ds.data,
                        borderColor: colorWrite,
                        backgroundColor: colorWrite.replace('0.8', '0.15'),
                        borderWidth: Base.STYLE.borderWidth,
                        fill: false,
                        tension: Base.STYLE.tension,
                        pointRadius: Base.STYLE.pointRadius,
                        pointHitRadius: Base.STYLE.pointHitRadius,
                        spanGaps: Base.STYLE.lineSpanGaps,
                    });
                });

                window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                    type: 'line',
                    data: { labels: labels, datasets: datasets },
                    options: Base.baseOptions({
                        unit: unit,
                        legend: Base.legendOptions({ fontSize: 10 }),
                        tooltip: Base.tooltipMulti(function (y, label) {
                            return label + ': ' + (y !== null ? y.toFixed(1) + unit : '—');
                        }),
                    }),
                });
            })
            .catch(function (e) {
                console.error('Failed to load dual chart ' + metricRead + '/' + metricWrite + ':', e);
                ctx.parentElement.innerHTML = Base.noDataMessage();
            });
    }

    // ---------------------------------------------------------------
    // loadChartMultiGpu — N-series from multi_gpu
    // ---------------------------------------------------------------
    // One line per GPU, identified by GPU UUID + model name in the
    // label. The label is shortened in the legend (12 chars of UUID +
    // model name) so the legend stays readable.
    function loadChartMultiGpu(canvasId, metric, uuid, range, unit) {
        var ctx = document.getElementById(canvasId);
        if (!ctx) return Promise.resolve();

        var url = Base.buildChartUrl(uuid, range, { metric: metric, extra: { multi_gpu: 'true' } });

        return fetchChartData(url).then(function (data) {
            Base.safeDestroy(canvasId, window.GRM.ChartRuntime.instances);

            window.GRM.ChartRuntime.instances[canvasId] = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: data.datasets.map(function (ds, i) {
                        var color = Colors.colorAt(Colors.GPU_COLORS, i);
                        return {
                            label: ds.label,  // GPU UUID + model
                            data: ds.data,
                            borderColor: color.border,
                            backgroundColor: color.bg,
                            borderWidth: Base.STYLE.borderWidth,
                            fill: false,
                            tension: Base.STYLE.tension,
                            pointRadius: Base.STYLE.pointRadius,
                            pointHitRadius: Base.STYLE.pointHitRadius,
                            spanGaps: Base.STYLE.lineSpanGaps,
                        };
                    }),
                },
                options: Base.baseOptions({
                    unit: unit,
                    legend: Base.legendOptions({
                        fontSize: 10,
                        // Label format: "GPU-a322cff7-...-b676c04a38aa RTX 3060"
                        // Result:      "GPU-a322cff… RTX 3060"
                        // (truncate the UUID part to 12 chars)
                        generateLabels: function (chart) {
                            var original = Chart.defaults.plugins.legend.labels.generateLabels(chart);
                            return original.map(function (label) {
                                if (!label.text) return label;
                                var spaceIdx = label.text.indexOf(' ');
                                if (spaceIdx > 0) {
                                    var uuidPart = label.text.substring(0, spaceIdx);
                                    var modelPart = label.text.substring(spaceIdx + 1);
                                    if (uuidPart.length > 12) {
                                        label.text = uuidPart.substring(0, 12) + '… ' + modelPart;
                                    }
                                } else if (label.text.length > 12) {
                                    label.text = label.text.substring(0, 12) + '…';
                                }
                                return label;
                            });
                        }
                    }),
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            title: function (items) { return items[0].label; },
                            label: function (ctx) {
                                return ctx.dataset.label + ': ' +
                                       (ctx.parsed.y !== null ? ctx.parsed.y.toFixed(1) + unit : '—');
                            },
                        },
                    },
                }),
            });
        }).catch(function (e) {
            console.error('Failed to load multi-GPU chart ' + metric + ':', e);
            ctx.parentElement.innerHTML = Base.noDataMessage();
        });
    }

    // Public API
    window.GRM = window.GRM || {};
    window.GRM.ChartLoaders = {
        fetchChartData: fetchChartData,
        metricToLabel: metricToLabel,
        loadChart: loadChart,
        loadChartLoadAvg: loadChartLoadAvg,
        loadChartMemSwap: loadChartMemSwap,
        loadChartNetworkCombined: loadChartNetworkCombined,
        loadChartMultiKey: loadChartMultiKey,
        loadChartMultiKeyDual: loadChartMultiKeyDual,
        loadChartMultiGpu: loadChartMultiGpu,
    };
})();
