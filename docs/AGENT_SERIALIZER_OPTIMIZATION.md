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

## Benefits of Agent-side Calculation

1. **Reduced database queries**: No need to fetch `LatestSnapshot` for delta calculation
2. **Reduced payload size**: Send deltas instead of large cumulative counters
3. **More accurate timing**: Agent knows exact time between collections
4. **Lower server CPU**: No delta math during ingest

## Implementation Notes

- Agent would need to track state across collection cycles
- Risk: Agent restart loses state → delta would be wrong on first cycle
- Mitigation: Server handles negative deltas gracefully (wraparound detection)