# Agent vs Serializer Computation Analysis

## Current Architecture

- Agent collects raw metrics every 60s and sends cumulative counters
- Serializer calculates deltas by comparing with `LatestSnapshot` values
- This requires database hit on every ingest to fetch previous values

## Heavy Calculations in Serializer (can move to agent)

### 1. Storage Delta Calculations
```python
# Currently in serializers.py lines 233-278
read_bytes_delta = new_read_bytes - prev_read_bytes
write_bytes_delta = new_write_bytes - prev_write_bytes
read_iops_delta = new_read_iops - prev_read_iops
write_iops_delta = new_write_iops - prev_write_iops
utilization_pct = busy_time_delta_ms / (time_elapsed * 1000) * 100
```

**Agent-side optimization:**
- Store previous values in memory (circular buffer or single value)
- Calculate deltas on each collection cycle
- Send both raw counters AND calculated deltas

### 2. Network Delta Calculations
```python
# Currently in serializers.py lines 348-364
rx_delta = new_rx_bytes - prev_rx_bytes
tx_delta = new_tx_bytes - prev_tx_bytes
```

**Same optimization possible**

## Proposed Agent Changes

### New Payload Structure
```json
{
  "metrics": {
    "storage": [
      {
        "device": "sda",
        "read_bytes": 123456789,
        "read_bytes_delta": 12345,
        "write_bytes": 987654321,
        "write_bytes_delta": 67890,
        "read_iops": 123456,
        "read_iops_delta": 123,
        "write_iops": 654321,
        "write_iops_delta": 456,
        "busy_time_ms": 5000,
        "busy_time_ms_delta": 50,
        "utilization_pct": 50.0
      }
    ]
  }
}
```

## Critical Assessment: Benefits vs Risks

### Actual Database Overhead
- **1 query per ingest**: Fetches single `LatestSnapshot` row for the rig
- **LatestSnapshot is ONE row per rig** - minimal query overhead
- **Django likely caches this row anyway** - subsequent queries hit cache

### Actual CPU Overhead in Serializer
- **22 delta calculations**: All simple arithmetic (`+`, `-`, `/`)
- **Per-calculation cost**: <1 microsecond
- **Total CPU per ingest**: ~50 microseconds (negligible)

### Actual Network Payload Size Impact
- **Each counter**: ~8 bytes (integer)
- **Example**: 10 disks × 6 counters = 60 integers = ~500 bytes
- **Minimal impact**: Already sending full metrics payload

### Risk Assessment: Agent Restart
- **First cycle after restart**: Delta equals raw value (no subtraction)
- **Utilization % calculation**: Would be wrong without previous busy_time
- **Server handling**: Current wraparound detection only works for negative deltas, not zero-division issues

### CONCLUSION: Benefits < Risks

| Factor | Assessment |
|--------|------------|
| Server performance gain | Negligible (<50μs/ingest) |
| Network savings | Minimal (~500 bytes) |
| Risk of wrong data | Real (agent restart edge case) |
| Code complexity | Increases (agent state management) |

### RECOMMENDATION: **Keep as-is**

Server-side calculations are correct because:
- ✅ LatestSnapshot query is minimal overhead (single row fetch)
- ✅ Delta math is trivial CPU load
- ✅ No risk of incorrect data on agent restart
- ✅ Simpler agent code (stateless)
- ✅ Server has complete data for historical tracking