# ⚡ Power Consumption & Cost Tracking — Simplified Plan

## Design Decisions

### 1. RAM + Disks + Motherboard = Flat 50W
**Rationale:** RAM (~8-16W), disks (~5-15W), motherboard/chipset (~10-15W), fans (~5-10W) together draw ~30-50W. This is negligible compared to GPUs (150-350W each). A flat 50W estimate keeps the model simple without meaningful accuracy loss.

### 2. GPU Power = Direct Measurement (Already Available)
**Rationale:** The agent already collects `power_draw_w` from nvidia-smi via pynvml. This is accurate (±2-5%). For multi-GPU rigs, sum all GPUs.

### 3. CPU Power = RAPL (Intel/AMD Running Average Power Limit)
**Rationale:** RAPL is the only reliable way to measure CPU power without external hardware. It's built into modern Intel (Sandy Bridge+) and AMD (Zen 2+) CPUs and exposed via Linux sysfs.

## CPU Power Measurement via RAPL

### What is RAPL?
- Running Average Power Limit — hardware energy counters built into Intel/AMD CPUs
- Exposed via Linux sysfs at `/sys/class/powercap/intel-rapl/` (Intel) or `/sys/class/powercap/amd-rapl/` (AMD)
- Reports energy in Joules, which can be converted to Watts
- No special permissions needed (readable by all users since Linux 3.13)
- Accuracy: ±3-5% for package power

### Sysfs Paths
```
/sys/class/powercap/intel-rapl:0/energy_uj          # Energy in microjoules
/sys/class/powercap/intel-rapl:0/max_energy_range_uj # Max energy counter value
/sys/class/powercap/intel-rapl:0/name                # "package-0"
```

### Reading CPU Power (Python)
```python
import time
import os

def read_cpu_power_w():
    """Read CPU power from RAPL sysfs. Returns watts or None if unavailable."""
    rapl_path = "/sys/class/powercap/intel-rapl:0/energy_uj"
    if not os.path.exists(rapl_path):
        return None  # RAPL not available
    
    # Read energy at two time points
    with open(rapl_path) as f:
        energy_start = int(f.read())
    time.sleep(0.1)  # 100ms sample window
    with open(rapl_path) as f:
        energy_end = int(f.read())
    
    # Handle counter wraparound
    max_path = "/sys/class/powercap/intel-rapl:0/max_energy_range_uj"
    with open(max_path) as f:
        max_energy = int(f.read())
    
    if energy_end < energy_start:
        energy_end += max_energy
    
    # Convert to watts
    energy_joules = (energy_end - energy_start) / 1_000_000
    power_watts = energy_joules / 0.1  # 0.1 second sample
    return power_watts
```

### RAPL Availability
| Platform | Support | Notes |
|---|---|---|
| Intel Sandy Bridge+ (2011+) | ✅ Full | Package, PP0 (cores), PP1 (uncore) |
| AMD Zen 2+ (2019+) | ✅ Full | Package power via `amd-rapl` |
| AMD Zen / Zen+ (2017-2019) | ⚠️ Partial | May need kernel module |
| Older CPUs | ❌ None | Fallback to estimation |
| VMs | ❌ None | RAPL not virtualized |

### Fallback for Non-RAPL Systems
If RAPL is not available, estimate CPU power:
```python
def estimate_cpu_power_w(cpu_utilization):
    """Estimate CPU power when RAPL is unavailable."""
    # Default TDP for common CPUs (can be overridden per rig)
    DEFAULT_TDP_W = 65
    
    # Power scales roughly linearly with utilization
    # At idle: ~10% of TDP, at full load: ~100% of TDP
    # Simple linear model: power = tdp × (0.1 + 0.9 × util)
    return DEFAULT_TDP_W * (0.1 + 0.9 * cpu_utilization)
```

## Simplified Power Model

### Formula
```
total_dc_power_w = gpu_power_sum + cpu_power + 50W
total_ac_power_w = total_dc_power_w / 0.90  # 90% PSU efficiency (Gold)

Where:
- gpu_power_sum: Sum of all GPU power_draw_w (from nvidia-smi, already collected)
- cpu_power: From RAPL (measured) or estimated from utilization
- 50W: Flat estimate for RAM + disks + motherboard + fans
- 0.90: PSU efficiency at typical load (80 Plus Gold)
```

### Example Calculation
```
Rig with:
- 2× RTX 3060 (170W each) = 340W
- Ryzen 7 5700X (RAPL reports 45W)
- RAM + disks + motherboard = 50W (flat)

Total DC: 340 + 45 + 50 = 435W
Total AC: 435 / 0.90 = 483W (from wall)

At $0.12/kWh:
Cost per hour: 0.483 × 0.12 = $0.058/hr
Cost per day: $0.058 × 24 = $1.39/day
Cost per month: $1.39 × 30 = $41.70/month
```

## Data Model (Simplified)

### Rig Model Additions
```python
class Rig(models.Model):
    # ... existing fields ...
    
    # Power configuration
    psu_wattage = models.PositiveIntegerField(
        default=750, 
        help_text="PSU rated wattage in watts"
    )
    electricity_rate_kwh = models.DecimalField(
        max_digits=6, decimal_places=4, 
        default=0.1200,
        help_text="Electricity cost per kWh"
    )
    cpu_tdp_w = models.PositiveIntegerField(
        default=65,
        help_text="CPU TDP in watts (for estimation fallback)"
    )
```

### PowerReading Model (Simplified)
```python
class PowerReading(models.Model):
    rig = models.ForeignKey(Rig, on_delete=models.CASCADE, related_name='power_readings')
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    
    # GPU (measured)
    gpu_power_w = models.FloatField(default=0)  # Sum of all GPUs
    
    # CPU (measured via RAPL or estimated)
    cpu_power_w = models.FloatField(default=0)
    cpu_power_source = models.CharField(max_length=10, default='rapl', choices=[
        ('rapl', 'RAPL (measured)'),
        ('estimate', 'Estimated from utilization'),
    ])
    
    # Flat estimate for other components
    other_power_w = models.FloatField(default=50)  # RAM + disks + MB + fans
    
    # Totals
    total_dc_power_w = models.FloatField(default=0)  # GPU + CPU + other
    total_ac_power_w = models.FloatField(default=0)  # After PSU efficiency
    
    class Meta:
        db_table = 'metrics_power_reading'
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['rig', '-timestamp'])]
```

## Agent Changes

### New: CPU Power Collection
```python
def collect_cpu_power():
    """Try RAPL first, fall back to None."""
    try:
        rapl_base = "/sys/class/powercap"
        # Find RAPL zone (intel-rapl:0 or amd-rapl:0)
        for entry in os.listdir(rapl_base):
            if entry.startswith("intel-rapl:") or entry.startswith("amd-rapl:0"):
                energy_path = os.path.join(rapl_base, entry, "energy_uj")
                if os.path.exists(energy_path):
                    return read_rapl_power(energy_path)
    except (OSError, PermissionError):
        pass
    return None  # RAPL not available, server will estimate
```

### Payload Addition
```json
{
  "power": {
    "gpu_power_w": 340.5,
    "cpu_power_w": 45.2,
    "cpu_power_source": "rapl"
  }
}
```

If RAPL unavailable, agent sends:
```json
{
  "power": {
    "gpu_power_w": 340.5,
    "cpu_power_w": null,
    "cpu_power_source": "unavailable"
  }
}
```

## Server-Side Processing

```python
def process_power(rig, power_data):
    gpu_power = power_data.get('gpu_power_w', 0)
    cpu_power = power_data.get('cpu_power_w')
    cpu_source = power_data.get('cpu_power_source', 'unavailable')
    
    # If RAPL not available, estimate from utilization
    if cpu_power is None:
        cpu_util = get_cpu_utilization()  # Already collected
        cpu_power = rig.cpu_tdp_w * (0.1 + 0.9 * cpu_util)
        cpu_source = 'estimate'
    
    # Flat estimate for other components
    other_power = 50  # RAM + disks + MB + fans
    
    # Totals
    total_dc = gpu_power + cpu_power + other_power
    psu_efficiency = 0.90  # 80 Plus Gold
    total_ac = total_dc / psu_efficiency
    
    PowerReading.objects.create(
        rig=rig,
        gpu_power_w=gpu_power,
        cpu_power_w=cpu_power,
        cpu_power_source=cpu_source,
        other_power_w=other_power,
        total_dc_power_w=total_dc,
        total_ac_power_w=total_ac,
    )
```

## Dashboard UI (Simplified)

### Live Metrics Card
```
┌─────────────────────────────────┐
│ ⚡ Power: 483W (AC)             │
│ GPU: 340W  CPU: 45W  Other: 50W│
│ Cost: $0.058/hr | $1.39/day    │
└─────────────────────────────────┘
```

### Fleet Overview
- New "Power [W]" column showing total AC power
- Sortable, color-coded (green <200W, yellow 200-400W, red >400W)

### Rig Detail
- Power breakdown bar (GPU / CPU / Other)
- Power trend chart (historical)
- kWh and cost summary (today, month)
- RAPL status indicator (measured vs estimated)

## Edge Cases

| Edge Case | Handling |
|---|---|
| RAPL not available (old CPU, VM) | Estimate from CPU utilization × TDP |
| GPU power not reported | Show "N/A" for GPU, still show CPU + other |
| Multi-GPU | Sum all GPU power_draw_w |
| Agent offline | No new readings, show "stale" indicator |
| PSU wattage unknown | Default 750W, user can configure |
| Electricity rate unknown | Default $0.12/kWh, user can configure |

## Implementation Plan

### Phase 1: Agent Changes (1 day)
- Add RAPL reading to agent
- Add power data to payload
- Handle RAPL unavailable gracefully

### Phase 2: Server Processing (1 day)
- Add PowerReading model
- Process power data in IngestView
- Store readings with source (rapl/estimate)

### Phase 3: Dashboard UI (1 day)
- Power card in Live Metrics
- Power column in Fleet Overview
- Power breakdown in Rig Detail

### Phase 4: Cost Tracking (1 day)
- kWh calculation (trapezoidal integration)
- Cost display (hourly, daily, monthly)
- User configuration (electricity rate, PSU wattage)

**Total: ~4 days**
