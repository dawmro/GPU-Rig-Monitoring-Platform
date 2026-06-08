# PCIe Link Info — Implementation Plan

## Data Source

### nvidia-smi query (human-readable)
```
nvidia-smi --query-gpu=pcie.link.gen.max,pcie.link.gen.current --format,csv
```
Output example:
```
4, 4
```
Meaning: Max Gen 4, Current Gen 4

### nvidia-smi -q (verbose)
```
nvidia-smi -q -d PCI
```
Output includes:
```
    PCIe Generation
        Max                 : 4
        Current             : 4
    Link Width
        Max                 : 16x
        Current             : 16x
```

### pynvml (NVML API) — preferred for agent
```python
import pynvml
pynvml.nvmlInit()
handle = pynvml.nvmlDeviceGetHandleByIndex(i)

# Current link generation (e.g., 1=Gen1, 2=Gen2, 3=Gen3, 4=Gen4, 5=Gen5)
current_gen = pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)

# Max link generation supported by GPU
max_gen = pynvml.nvmlDeviceGetMaxPcieLinkGeneration(handle)

# Current link width (e.g., 1=x1, 4=x4, 8=x8, 16=x16)
current_width = pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)

# Max link width supported by GPU
max_width = pynvml.nvmlDeviceGetMaxPcieLinkWidth(handle)
```

All 4 functions are available in the `nvidia-ml-py3` package (already a dependency).

## Data to Collect Per GPU

| Field | Source | Example | Meaning |
|---|---|---|---|
| `pcie_current_gen` | nvmlDeviceGetCurrPcieLinkGeneration | 4 | Running at Gen 4 |
| `pcie_max_gen` | nvmlDeviceGetMaxPcieLinkGeneration | 4 | GPU supports up to Gen 4 |
| `pcie_current_width` | nvmlDeviceGetCurrPcieLinkWidth | 16 | Running at x16 |
| `pcie_max_width` | nvmlDeviceGetMaxPcieLinkWidth | 16 | GPU supports up to x16 |

## Why This Matters

A GPU that should run at Gen4 x16 but is running at Gen1 x1 has a **configuration problem**:
- Loose riser cable
- Wrong PCIe slot
- BIOS settings
- Hardware fault

This directly impacts performance: Gen1 x1 = ~250 MB/s, Gen4 x16 = ~32 GB/s.

## Why Not nvmlDeviceGetPcieThroughput

NVML has `nvmlDeviceGetPcieThroughput` which returns PCIe bandwidth utilization in KB/s (read and write). This is useful for monitoring but is a rate, not a capability indicator. We want to detect capability misconfiguration first. Throughput monitoring could be a follow-up feature.

## Implementation

### 1. Agent Changes (`agent/run.py`)

Add PCIe link info to `collect_gpus()`:

```python
# Collect PCIe link info
try:
    current_gen = pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)
    max_gen = pynvml.nvmlDeviceGetMaxPcieLinkGeneration(handle)
    current_width = pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)
    max_width = pynvml.nvmlDeviceGetMaxPcieLinkWidth(handle)
except pynvml.NVMLError:
    current_gen = max_gen = current_width = max_width = None

gpu_data['pcie_current_gen'] = current_gen
gpu_data['pcie_max_gen'] = max_gen
gpu_data['pcie_current_width'] = current_width
gpu_data['pcie_max_width'] = max_width
```

Windows agent needs the same changes.

### 2. Server Changes (`metrics_app/models.py`)

Add fields to `GPUMetric`:
```python
pcie_current_gen = models.PositiveSmallIntegerField(null=True)
pcie_max_gen = models.PositiveSmallIntegerField(null=True)
pcie_current_width = models.PositiveSmallIntegerField(null=True)
pcie_max_width = models.PositiveSmallIntegerField(null=True)
```

### 3. Server Changes (`metrics_app/serializers.py`

Add to GPU data in `process_ingest()`:
```python
'pcie_current_gen': gpu.get('pcie_current_gen'),
'pcie_max_gen': gpu.get('pcie_max_gen'),
'pcie_current_width': gpu.get('pcie_current_width'),
'pcie_max_width': gpu.get('pcie_max_width'),
```

### 4. Dashboard Changes

**Live Metrics — GPU card:** Add PCIe info line below existing GPU stats:
```
PCIe: Gen4 x16 (max Gen4 x16) ← green if current==max
PCIe: Gen1 x1 (max Gen4 x16) ← red if current < max (degraded)
```

**Fleet Overview — GPU column:** Add optional PCIe status indicator.

## Detection Logic

```python
def pcie_status(current_gen, max_gen, current_width, max_width):
    """Return status: 'ok', 'degraded', or 'unknown'."""
    if any(v is None for v in [current_gen, max_gen, current_width, max_width]):
        return 'unknown'
    if current_gen < max_gen or current_width < max_width:
        return 'degraded'
    return 'ok'

def pcie_label(current_gen, max_gen, current_width, max_width):
    """Return human-readable label like 'Gen4 x16' or 'Gen1 x1 (max Gen4 x16)'."""
    if current_gen is None or current_width is None:
        return 'N/A'
    current = f"Gen{current_gen} x{current_width}"
    if max_gen is None or max_width is None:
        return current
    if current_gen < max_gen or current_width < max_width:
        return f"{current} (max Gen{max_gen} x{max_width})"
    return current
```

## Display Format

| Status | Color | Example |
|---|---|---|
| OK (current == max) | Green | `Gen4 x16 ✓` |
| Degraded | Red | `Gen1 x1 ⚠ max Gen4 x16` |
| Unknown | Gray | `N/A` |

## Edge Cases

| Case | Handling |
|---|---|
| pynvml call fails | Return None for all fields, display "N/A" |
| GPU doesn't support PCIe reporting | Same as above |
| Width is 0 | Display "N/A" (shouldn't happen but guard anyway) |
| Gen > 5 (future) | Display as-is (Gen6, Gen7 etc.) |
| Width not power of 2 | Display as-is (rare but possible) |

## Files Changed

| File | Change |
|---|---|
| `agent/run.py` | Add PCIe collection in `collect_gpus()` |
| `agent_windows/run.py` | Same for Windows |
| `metrics_app/models.py` | Add 4 fields to `GPUMetric` |
| `metrics_app/serializers.py` | Store PCIe fields from payload |
| `gpu_monitor/templates/dashboard/_metrics_cards.html` | Display PCIe info in GPU card |
| Migration | New fields on GPUMetric |

## Payload Example (after change)

```json
{
    "uuid": "GPU-a322cff7-19cf-f056-4a38-b676c04a38aa",
    "model": "NVIDIA GeForce RTX 3060",
    "mem_total_mb": 12288,
    "mem_used_mb": 745,
    "mem_free_mb": 11542,
    "mem_util_pct": 6.1,
    "gpu_util_pct": 2,
    "temp_c": 42,
    "fan_speed_pct": 0,
    "power_draw_w": 9.436,
    "power_limit_w": 170.0,
    "pcie_current_gen": 4,
    "pcie_max_gen": 4,
    "pcie_current_width": 16,
    "pcie_max_width": 16
}
```
