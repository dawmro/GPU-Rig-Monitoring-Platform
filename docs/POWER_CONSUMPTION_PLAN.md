# ⚡ Power Consumption & Cost Tracking — Detailed Implementation Plan

## Problem Statement
GPU rigs consume significant electricity. Users need to track power consumption and estimate electricity costs for profitability analysis. The agent already collects GPU power_draw_w, but total system power (CPU, RAM, motherboard, disks, fans) is not measured directly.

## Research Summary

### How Other Systems Do It

**1. Direct Measurement (Most Accelligent)**
- Smart PDUs (Raritan, APC) measure per-outlet power at the rack level
- Wall meters (Kill-A-Watt) measure total system draw at the PSU inlet
- IPMI/BMC on server motherboards reports CPU + motherboard power
- Accuracy: ±1-5%, but requires hardware

**2. Software Estimation (What We'll Do)**
- CPU: Linear model based on TDP × utilization (research shows <5% error)
- GPU: Direct from nvidia-smi / pynvml (already collected)
- RAM: ~3-5W per DIMM, scales slightly with utilization
- Motherboard: Flat 25-50W (chipset, fans, peripherals)
- Disks: ~5-8W per HDD, ~2-3W per SSD (active), ~0.5W idle
- PSU efficiency: Apply curve based on load percentage

**3. Hybrid Approach**
- Use software estimation as default
- Allow users to provide actual PSU wattage readings for calibration
- Store calibration offset per rig

## Estimation Approaches Compared

### Approach A: Flat Estimates (Simplest)
```
total_power = gpu_power + cpu_tdp × cpu_util + flat_50W
```
- **Pros:** Simple, no calibration needed, works for all hardware
- **Cons:** ±20-30% error, doesn't account for disk count, RAM, PSU efficiency
- **Accuracy:** Low, but "good enough" for cost estimation

### Approach B: Component-Based Estimation (Recommended)
```
cpu_power = cpu_tdp × cpu_util × cpu_power_factor
ram_power = ram_dimm_count × 4W × (0.5 + 0.5 × mem_util)
disk_power = sum(disk_active_watts × disk_util + disk_idle_watts × (1 - disk_util))
mb_power = 35W (flat estimate for chipset, fans, peripherals)
total_dc_power = gpu_power + cpu_power + ram_power + disk_power + mb_power
total_ac_power = total_dc_power / psu_efficiency(load_percent)
```
- **Pros:** ±10-15% error, accounts for component count and utilization
- **Cons:** Needs CPU TDP, disk count, RAM count from agent
- **Accuracy:** Good for cost estimation

### Approach C: Calibrated Estimation (Most Accurate)
```
Same as Approach B, but:
1. User provides actual wall meter reading at a known load point
2. System calculates calibration offset: offset = actual_watts / estimated_watts
3. All future estimates multiplied by offset
4. User can recalibrate anytime
```
- **Pros:** ±5-10% error after calibration, self-correcting
- **Cons:** Requires user action to calibrate, more complex
- **Accuracy:** Very good

### Approach D: ML-Based (Overkill for v1)
- Train a model on known hardware configurations
- Predict power based on component specs + utilization
- **Pros:** Could be very accurate with enough data
- **Cons:** Complex, needs training data, overkill for this use case

## Recommended Approach: B + C Hybrid

**Default:** Component-Based Estimation (B) with sensible defaults
**Optional:** User calibration (C) for improved accuracy

## Detailed Power Model

### CPU Power Estimation
```
cpu_power_w = cpu_tdp × cpu_utilization × power_factor

Where:
- cpu_tdp: From CPU model database (e.g., Ryzen 7 5700X = 65W, i9-13900K = 125W)
- cpu_utilization: From agent (0.0 to 1.0)
- power_factor: 0.85 (CPU doesn't draw full TDP at partial load due to voltage scaling)

Fallback if TDP unknown: cpu_power_w = 65W × cpu_utilization
```

**Research backing:** ACM study shows CPU power is highly linear with utilization (<5% error). The power_factor accounts for the fact that power doesn't scale perfectly linearly due to voltage/frequency curves.

### GPU Power Estimation
```
gpu_power_w = power_draw_w (from pynvml, already collected)

For multi-GPU: sum all GPUs
```
**Accuracy:** ±2-5% (nvidia-smi reports actual power draw)

### RAM Power Estimation
```
ram_power_w = dimm_count × per_dimm_w × load_factor

Where:
- dimm_count: From system info (e.g., 4 DIMMs)
- per_dimm_w: 4W (DDR4 typical), 5W (DDR5 typical)
- load_factor: 0.5 + 0.5 × memory_utilization (idle = 50%, full = 100%)

Fallback: 4 DIMMs × 4W = 16W (typical system)
```

### Disk Power Estimation
```
disk_power_w = sum for each disk:
  if disk_busy: active_watts (6W HDD, 3W SSD)
  if disk_idle: idle_watts (1.5W HDD, 0.5W SSD)

Where disk_busy is determined by disk_utilization from agent
```

### Motherboard / Peripheral Power
```
mb_power_w = 35W (flat estimate)

This covers:
- Chipset: 5-15W
- Fans: 3-10W (1-3 fans at 3W each)
- Network: 2-5W
- USB peripherals: 2-5W
- Losses: 5-10W
```

### PSU Efficiency
```
psu_efficiency = f(load_percent) based on 80 Plus rating

80 Plus Gold efficiency curve:
- 20% load: 87%
- 50% load: 90%
- 80% load: 87%
- 100% load: 85%

Default: 90% (Gold) — user can select Bronze (85%), Silver (87%), Gold (90%), Platinum (92%), Titanium (94%)

total_ac_power = total_dc_power / psu_efficiency
```

**Note:** PSU efficiency matters for cost calculation. A 500W DC load at 90% efficiency draws 555W from the wall.

## Data Model Changes

### New Fields on Rig Model
```python
# Power estimation configuration
cpu_tdp_w = models.PositiveIntegerField(default=65, help_text="CPU TDP in watts")
ram_dimm_count = models.PositiveSmallIntegerField(default=4, help_text="Number of RAM DIMMs")
psu_efficiency_rating = models.CharField(max_length=20, default='gold', choices=[
    ('bronze', '80+ Bronze (85%)'),
    ('silver', '80+ Silver (87%)'),
    ('gold', '80+ Gold (90%)'),
    ('platinum', '80+ Platinum (92%)'),
    ('titanium', '80+ Titanium (94%)'),
])
power_calibration_factor = models.FloatField(default=1.0, help_text="Multiplier for calibration")
```

### New Model: PowerReading
```python
class PowerReading(models.Model):
    rig = models.ForeignKey(Rig, on_delete=models.CASCADE, related_name='power_readings')
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    
    # Measured / Estimated values
    gpu_power_w = models.FloatField(default=0)  # From nvidia-smi
    cpu_power_w = models.FloatField(default=0)  # Estimated
    ram_power_w = models.FloatField(default=0)  # Estimated
    disk_power_w = models.FloatField(default=0)  # Estimated
    mb_power_w = models.FloatField(default=35)  # Flat estimate
    
    # Totals
    total_dc_power_w = models.FloatField(default=0)  # Sum of components
    total_ac_power_w = models.FloatField(default=0)  # After PSU efficiency
    psu_efficiency = models.FloatField(default=0.9)  # Applied efficiency
    
    # Source
    is_estimated = models.BooleanField(default=True)  # True = estimated, False = calibrated
    
    class Meta:
        db_table = 'metrics_power_reading'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['rig', '-timestamp']),
        ]
```

### New Field on User Model
```python
electricity_rate_kwh = models.DecimalField(
    max_digits=6, decimal_places=4, default=0.1200,
    help_text="Electricity cost per kWh in your currency"
)
currency = models.CharField(max_length=3, default='USD')
```

## Agent Changes

### New Collector: power_collector.py
```python
def collect_power_data():
    """Collect and estimate power consumption."""
    data = {
        'gpu_power': get_gpu_power(),  # Already collected
        'cpu_util': get_cpu_utilization(),  # Already collected
        'mem_util': get_memory_utilization(),  # Already collected
        'disk_io': get_disk_io_stats(),  # Already collected
        'cpu_model': get_cpu_model(),  # Already collected
        'ram_total_gb': get_ram_total(),  # Already collected
        'disk_count': get_disk_count(),  # NEW: count disks
    }
    return data
```

### Payload Addition
```json
{
  "power": {
    "gpu_power_w": 170.5,
    "cpu_util": 0.45,
    "mem_util": 0.62,
    "disk_count": 3,
    "cpu_model": "AMD Ryzen 7 5700X 8-Core Processor"
  }
}
```

## Server-Side Processing

### IngestView Addition
```python
def process_power_data(rig, power_data):
    """Process power data from agent payload."""
    # Get rig's power config
    config = rig.power_config
    
    # Estimate CPU power
    cpu_tdp = config.cpu_tdp_w or get_cpu_tdp_from_model(power_data['cpu_model'])
    cpu_power = cpu_tdp * power_data['cpu_util'] * 0.85
    
    # Estimate RAM power
    ram_power = config.ram_dimm_count * 4 * (0.5 + 0.5 * power_data['mem_util'])
    
    # Estimate disk power
    disk_power = estimate_disk_power(power_data['disk_io'], power_data['disk_count'])
    
    # Motherboard flat estimate
    mb_power = 35
    
    # GPU power (measured)
    gpu_power = power_data['gpu_power_w']
    
    # Total DC power
    total_dc = gpu_power + cpu_power + ram_power + disk_power + mb_power
    
    # Apply PSU efficiency
    psu_eff = get_psu_efficiency(config.psu_efficiency_rating, total_dc / 500)  # Assuming 500W PSU
    total_ac = total_dc / psu_eff
    
    # Apply calibration factor
    total_ac *= config.power_calibration_factor
    
    # Store reading
    PowerReading.objects.create(
        rig=rig,
        gpu_power_w=gpu_power,
        cpu_power_w=cpu_power,
        ram_power_w=ram_power,
        disk_power_w=disk_power,
        mb_power_w=mb_power,
        total_dc_power_w=total_dc,
        total_ac_power_w=total_ac,
        psu_efficiency=psu_eff,
        is_estimated=(config.power_calibration_factor == 1.0),
    )
```

## Dashboard UI

### Live Metrics Card
```
┌─────────────────────────────────┐
│ ⚡ Power Consumption            │
│                                 │
│ Current: 342W (DC) / 380W (AC) │
│ GPU: 170W  CPU: 28W  RAM: 12W  │
│ Disk: 8W  MB: 35W               │
│                                 │
│ Est. Cost: $0.046/hr           │
│ Today: 8.2 kWh ($0.98)         │
└─────────────────────────────────┘
```

### Historical Chart
- New "Power Consumption" chart in Historical Charts tab
- Shows total_ac_power_w over time
- Stacked area chart: GPU / CPU / RAM / Disk / MB
- Secondary Y-axis: estimated cost per hour

### Rig Detail Page
- Power breakdown bar chart (per-component)
- Total power trend over time
- kWh consumed (today, this month, total)
- Estimated cost (today, this month, total)
- Calibration UI: "Calibrate with actual meter reading"

### Fleet Overview
- New "Power [W]" column showing current total power draw
- Color-coded: green (<200W), yellow (200-400W), red (>400W)
- Sortable by power consumption

## Cost Calculation

### Real-Time Cost
```
cost_per_hour = total_ac_power_w / 1000 × electricity_rate_kwh
```

### Daily/Monthly Cost
```python
# Sum kWh for the period
readings = PowerReading.objects.filter(
    rig=rig,
    timestamp__gte=start_date,
    timestamp__lte=end_date
).order_by('timestamp')

# Trapezoidal integration for kWh
total_kwh = 0
for i in range(1, len(readings)):
    dt_hours = (readings[i].timestamp - readings[i-1].timestamp).total_seconds() / 3600
    avg_power_kw = (readings[i].total_ac_power_w + readings[i-1].total_ac_power_w) / 2 / 1000
    total_kwh += avg_power_kw * dt_hours

total_cost = total_kwh × electricity_rate_kwh
```

## Edge Cases

### Edge Case 1: Unknown CPU Model
**Problem:** CPU model string from agent doesn't match database
**Solution:** Use default TDP of 65W, allow user to set TDP manually

### Edge Case 2: Missing GPU Power Data
**Problem:** Some GPUs don't report power_draw_w (returns null)
**Solution:** Estimate GPU power from GPU utilization × TDP (less accurate but functional)

### Edge Case 3: Variable PSU Load
**Problem:** PSU efficiency changes with load percentage
**Solution:** Use efficiency curve lookup based on current load % of rated PSU wattage

### Edge Case 4: Multi-GPU Rigs
**Problem:** 8 GPUs can draw 2000W+ total
**Solution:** Sum all GPU power draws, ensure PSU wattage is sufficient (warn if >80% of rated)

### Edge Case 5: Calibration Drift
**Solution:** Store calibration timestamp, warn if >30 days since last calibration

### Edge Case 6: Currency/Time Period
**Solution:** Store currency with rate, handle timezone for "today"/"this month" calculations

### Edge Case 7: Agent Offline
**Solution:** No new readings when agent is offline. Cost estimation uses last known reading (with "stale" indicator)

### Edge Case 8: Disk Hotplug
**Solution:** Disk count can change. Re-count on each ingest, update estimate dynamically.

## Implementation Phases

### Phase 1: Basic Estimation (1-2 days)
- Add power fields to Rig model
- Create PowerReading model
- Add power data collection to agent
- Basic estimation (flat 50W + GPU + CPU estimate)
- Simple "Power" card in Live Metrics

### Phase 2: Component-Based Estimation (1-2 days)
- Implement full component-based model
- CPU TDP database lookup
- RAM estimation based on DIMM count
- Disk estimation based on I/O activity
- PSU efficiency curve application

### Phase 3: Dashboard UI (1-2 days)
- Power breakdown in rig detail
- Historical power chart
- Cost calculation (daily, monthly)
- Fleet overview power column

### Phase 4: Calibration (1 day)
- Calibration UI in rig detail
- Calibration factor storage
- "Calibrate with actual meter reading" flow

### Phase 5: Polish (1 day)
- Export power/cost data to CSV
- Power alerts (e.g., "power draw exceeded 500W")
- Per-rig power configuration UI
- Documentation

**Total estimated effort: 5-7 days**
