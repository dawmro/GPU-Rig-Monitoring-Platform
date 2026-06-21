# Chart Aggregation Analysis Report

## Scope
Analyzed aggregation logic in:
1. `compact_data.py` — Compaction script (1-min → 1-hour buckets)
2. `ChartDataView` in `metrics_app/views.py` — Chart data endpoint

## Compaction Strategy (compact_data.py)

For each table, old data (>1 day) is aggregated into 1-hour buckets:

### metrics_storagemetric
| Field | Aggregation | Rationale |
|---|---|---|
| `usage_pct` | AVG | Percentage should be averaged |
| `temp_c` | AVG | Temperature should be averaged |
| `capacity_bytes` | LAST | Static value, take latest |
| `read_bytes_delta` | **SUM** | Delta counter — sum over hour = total bytes |
| `write_bytes_delta` | **SUM** | Delta counter — sum over hour = total bytes |
| `read_iops_delta` | **SUM** | Delta counter — sum over hour = total IOPS |
| `write_iops_delta` | **SUM** | Delta counter — sum over hour = total IOPS |
| `utilization_pct` | AVG | Percentage should be averaged |
| `read_bytes` | LAST | Cumulative counter — latest value |
| `write_bytes` | LAST | Cumulative counter — latest value |
| `read_iops` | LAST | Cumulative counter — latest value |
| `write_iops` | LAST | Cumulative counter — latest value |
| `busy_time_ms` | LAST | Cumulative counter — latest value |

### metrics_networkmetric
| Field | Aggregation | Rationale |
|---|---|---|
| `rx_bytes_delta` | **SUM** | Delta counter — sum over hour |
| `tx_bytes_delta` | **SUM** | Delta counter — sum over hour |
| `rx_errors` | **SUM** | Error count — sum over hour |
| `tx_errors` | **SUM** | Error count — sum over hour |
| `link_speed_mbps` | LAST | Static value |
| `ipv4` | LAST | Static value |

### metrics_gpumetric
| Field | Aggregation | Rationale |
|---|---|---|
| `gpu_util_pct` | AVG | Percentage |
| `gpu_temp_c` | AVG | Temperature |
| `fan_speed_pct` | AVG | Percentage |
| `mem_used_mb` | AVG | Memory usage |
| `power_draw_w` | AVG | Power average |
| `gpu_core_clock_mhz` | AVG | Clock average |
| `gpu_mem_clock_mhz` | AVG | Clock average |
| `power_limit_w` | LAST | Static |
| `pcie_*` | LAST | Static |
| `model` | LAST | Static |

### metrics_metricsnapshot
| Field | Aggregation | Rationale |
|---|---|---|
| `cpu_utilization_pct` | AVG | Percentage |
| `cpu_temp_c` | AVG | Temperature |
| `cpu_freq_current_mhz` | AVG | Frequency average |
| `cpu_freq_min_mhz` | LAST | Minimum (keep the min) |
| `cpu_freq_max_mhz` | LAST | Maximum (keep the max) |
| `mem_used_bytes` | AVG | Memory average |
| `swap_used_bytes` | AVG | Swap average |
| `uptime_s` | MAX | Keep max uptime |
| `error_count` | **SUM** | Total errors in hour |
| `status` | LAST | Latest status |

## Chart Data Strategy (ChartDataView)

Charts use two time ranges:
- **24h:** 1-minute buckets (raw data)
- **7d/30d:** 1-hour buckets (compacted data)

### Aggregation function selection:
```python
agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'error_frequency'} else Avg
```

### Per-metric analysis:

| Chart Metric | 24h (raw) | 7d/30d (compacted) | Correct? |
|---|---|---|---|
| CPU util/temp/freq | AVG of per-min values | AVG of hourly AVGs | ✅ |
| Memory bytes | AVG of per-min values | AVG of hourly AVGs | ✅ |
| Swap bytes | AVG of per-min values | AVG of hourly AVGs | ✅ |
| GPU temp/util/mem/power | AVG of per-min values | AVG of hourly AVGs | ✅ |
| GPU clocks | AVG of per-min values | AVG of hourly AVGs | ✅ |
| Disk usage % | AVG (direct field) | AVG of hourly AVGs | ✅ |
| Disk read/write bytes | AVG of per-min deltas | AVG of hourly SUMs | ✅ |
| Disk read/write IOPS | AVG of per-min deltas | AVG of hourly SUMs | ✅ |
| Disk utilization % | AVG (direct field) | AVG of hourly AVGs | ✅ |
| Network rx/tx bytes | SUM of per-min deltas | SUM of hourly SUMs | ✅ |
| Network errors | SUM of per-min values | SUM of hourly SUMs | ✅ |
| Error frequency | SUM of per-min counts | SUM of hourly SUMs | ✅ |
| Uptime | Raw values (no agg) | Raw values | ✅ |
| CPU load avg | Raw JSON parsing | Raw JSON parsing | ✅ |

## Key Insight: Why AVG on compacted SUMs is correct

For disk I/O in 7d/30d charts:
1. compact_data.py applies SUM over 60 rows → 1 hourly row with total bytes/IOPS
2. ChartDataView applies AVG over these hourly rows
3. Result: average bytes/hour or average IOPS/hour

This is semantically correct — the chart shows "average hourly throughput" which is what users expect.

## Conclusion

**Two bugs found and fixed:**

### Bug 1: cpu_freq_min_mhz / cpu_freq_max_mhz — WRONG aggregation in compact_data.py

**Before fix:**
```python
'cpu_freq_min_mhz': 'last', 'cpu_freq_max_mhz': 'last',
```

**After fix:**
```python
'cpu_freq_min_mhz': 'min', 'cpu_freq_max_mhz': 'max',
```

**Rationale:** When compacting 1-minute rows into 1-hour buckets:
- `cpu_freq_min_mhz` should use `MIN` to capture the minimum frequency in that hour
- `cpu_freq_max_mhz` should use `MAX` to capture the maximum frequency in that hour
- Using `LAST` only kept the last value, losing the actual min/max information

Also added `min` support to the SQL generation in `_compact_table()`.

### Bug 2: Disk Read/Write and IOPS deltas — WRONG aggregation in ChartDataView

**Before fix:**
```python
agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'error_frequency'} else Avg
```

**After fix:**
```python
agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'error_frequency', 'disk_read_bytes_delta', 'disk_write_bytes_delta', 'disk_read_iops_delta', 'disk_write_iops_delta'} else Avg
```

**Rationale:** Disk I/O deltas represent bytes/IOPS transferred since the last reading. When charting:
- **24h chart (1-min buckets):** Each bucket has 1 row. SUM = the delta itself = bytes/min. ✅
- **7d chart (1-hour buckets):** Each bucket has 60 rows (raw data) or 1 row (compacted). SUM of all deltas in the hour = total bytes/IOPS for that hour. ✅

Using `AVG` was wrong because:
- For raw data: AVG of 60 deltas = average bytes per minute (not total)
- For compacted data: AVG of 1 row = the SUM value (correct by accident)
- This caused the 24h chart to show much higher values than the 7d chart for the same period

**All other aggregations are correct:**
- compact_data.py uses appropriate aggregation per field type
- ChartDataView uses AVG for percentages/temperatures (correct for averaging across buckets)
- Network deltas: SUM in both compaction and charts → correct
- Error frequency: SUM in both → correct total error count
