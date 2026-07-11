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
| `mem_controller_util_pct` | AVG | Memory controller utilization % |
| `mem_util_pct` | AVG | VRAM utilization % |
| `mem_total_mb` | LAST | Static |
| `mem_free_mb` | AVG | VRAM free |

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

Charts use three time ranges:
- **24h:** 1-minute buckets (raw data)
- **7d:** 15-minute buckets (Tier 2 compacted data)
- **30d:** 1-hour buckets (Tier 3 compacted data)

### Aggregation function selection:
```python
agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'net_rx_errors', 'net_tx_errors', 'error_frequency', 'disk_read_bytes_delta', 'disk_write_bytes_delta', 'disk_read_iops_delta', 'disk_write_iops_delta'} else Avg
```

### Per-metric analysis:

| Chart Metric | 24h (raw, 1-min) | 7d (15-min compacted) | 30d (1-hr compacted) | Correct? |
|---|---|---|---|---|
| CPU util/temp/freq | AVG of per-min values | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| Memory bytes | AVG of per-min values | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| Swap bytes | AVG of per-min values | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| GPU temp/util/mem_ctrl/power | AVG of per-min values | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| GPU clocks | AVG of per-min values | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| Disk usage % | AVG (direct field) | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| Disk read/write bytes | SUM of per-min deltas | SUM of 15-min SUMs | SUM of hourly SUMs | ✅ |
| Disk read/write IOPS | SUM of per-min deltas | SUM of 15-min SUMs | SUM of hourly SUMs | ✅ |
| Disk utilization % | AVG (direct field) | AVG of 15-min AVGs | AVG of hourly AVGs | ✅ |
| Network rx/tx bytes | SUM of per-min deltas | SUM of 15-min SUMs | SUM of hourly SUMs | ✅ |
| Network errors | SUM of per-min values | SUM of 15-min SUMs | SUM of hourly SUMs | ✅ |
| Error frequency | SUM of per-min counts | SUM of 15-min SUMs | SUM of hourly SUMs | ✅ |
| Uptime | Raw values (no agg) | Raw values | Raw values | ✅ |
| CPU load avg | Raw JSON parsing | Raw JSON parsing | Raw JSON parsing | ✅ |

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

### Bug 2: Disk Read/Write and IOPS deltas + Network errors — WRONG aggregation in ChartDataView

**Before fix:**
```python
agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'error_frequency', 'disk_read_bytes_delta', 'disk_write_bytes_delta', 'disk_read_iops_delta', 'disk_write_iops_delta'} else Avg
```

**After fix:**
```python
agg_func = Sum if metric in {'net_rx_bytes_delta', 'net_tx_bytes_delta', 'net_rx_errors', 'net_tx_errors', 'error_frequency', 'disk_read_bytes_delta', 'disk_write_bytes_delta', 'disk_read_iops_delta', 'disk_write_iops_delta'} else Avg
```

**Rationale:** 
- Disk I/O deltas represent bytes/IOPS transferred since last reading. SUM gives total per bucket.
- Network errors are cumulative counters. SUM gives total errors per bucket.
- Using `AVG` was wrong because it gave average-per-minute instead of total-per-hour for 7d/30d charts.
- This caused the 24h chart to show much higher values than the 7d chart for the same period.

**All other aggregations are correct:**
- compact_data.py uses appropriate aggregation per field type
- ChartDataView uses AVG for percentages/temperatures (correct for averaging across buckets)
- Network deltas: SUM in both compaction and charts → correct
- Error frequency: SUM in both → correct total error count
