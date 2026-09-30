# Plan: Agent Additional Information Collection

**Branch:** `plan/agent-additional-info-collection`
**Created:** 2026-09-29
**Status:** Planning phase — awaiting approval

---

## 1. Current Collection Analysis

### 1.1 Agent Collectors (`agent/run.py`)

| Collector | Current Fields Collected | Source |
|-----------|-------------------------|--------|
| `collect_cpu()` | model, physical_cores, logical_cores, load_avg, utilization_pct, temp_c, freq (current/min/max) | psutil + cpuinfo + sysfs |
| `collect_memory()` | total_bytes, used_bytes, free_bytes, cached_bytes, swap_used_bytes, swap_total_bytes | psutil |
| `collect_motherboard()` | manufacturer, model, bios_version | `/sys/class/dmi/id/` |
| `collect_storage()` | device, mountpoint, fstype, capacity_bytes, usage_pct, temp_c, smart_health, read_bytes, write_bytes, read_iops, write_iops, busy_time_ms | psutil + smartctl + nvme CLI |
| `collect_network()` | interface, rx_bytes, tx_bytes, rx_errors, tx_errors, ipv4, link_speed_mbps | psutil + sysfs |
| `collect_gpus()` | uuid, model, mem_total_mb, mem_used_mb, mem_free_mb, mem_util_pct, mem_controller_util_pct, gpu_util_pct, temp_c, fan_speed_pct, power_draw_w, power_limit_w, pcie_current_gen, pcie_max_gen, pcie_current_width, pcie_max_width, gpu_core_clock_mhz, gpu_mem_clock_mhz | pynvml |
| `collect_gpu_processes()` | gpu_index, pid, type, name, gpu_mem_mb | nvidia-smi subprocess |
| `collect_docker()` | container_id, name, image, status, created, status_text, manifest (inspect), logs | docker CLI |
| `collect_top_processes()` | pid, name, cpu_pct, mem_pct, username, cmdline | psutil two-pass |
| `collect_software()` | hostname, os_distro, kernel, uptime_s, nvidia_driver, docker_version | platform + psutil + subprocess |
| `collect_errors()` | source, message, timestamp | journalctl |
| `collect_power()` | cpu_power_w, cpu_power_source, gpu_power_w, other_power_w, total_power_w | RAPL + pynvml + estimation |

### 1.2 Server Storage Models

| Model | Purpose | Key Fields |
|-------|---------|------------|
| `MetricSnapshot` | Time-series (1 row/rig/minute) | CPU, memory, power, has_active_job, error_count, uptime_s |
| `GPUMetric` | Time-series (1 row/GPU/minute) | GPU model, uuid, util, temp, power, clocks, PCIe |
| `StorageMetric` | Time-series (1 row/disk/minute) | capacity, usage, temp, smart, I/O deltas |
| `NetworkMetric` | Time-series (1 row/interface/minute) | rx/tx bytes, errors, deltas |
| `LatestSnapshot` | Denormalized latest state (1 row/rig) | All above as JSON arrays + static fields |
| `LatestDockerContainer` | Latest container state | container_id, name, image, status, manifest, logs |

### 1.3 Data Flow

```
Agent (run.py) → POST /api/v1/ingest/ → IngestSerializer.validate() → process_ingest()
    → MetricSnapshot.update_or_create()
    → GPUMetric.update_or_create() (per GPU)
    → StorageMetric.update_or_create() (per disk)
    → NetworkMetric.update_or_create() (per interface)
    → LatestSnapshot.update_or_create() (denormalized JSON arrays)
    → LatestDockerContainer delete + bulk_create
    → RigStatusEvent (status transitions)
    → Rig error_history_json, container_history_json (rolling dedup)
```

---

## 2. Additional Collectible Information

### 2.1 GPU Enhancements (via pynvml)

| Info | pynvml API | Storage Location | Notes |
|------|------------|------------------|-------|
| **GPU Brand** (GeForce/RTX/Tesla/Quadro) | `nvmlDeviceGetBrand()` | `GPUMetric.gpu_brand` + `LatestSnapshot.gpu_brands_json` | Distinguishes consumer vs datacenter/vGPU |
| **AIB Board Part Number** (e.g. "ASUS Astral") | `nvmlDeviceGetBoardPartNumber()` | `GPUMetric.gpu_board_part_number` + `LatestSnapshot.gpu_board_part_numbers_json` | Subvendor marketing name (ASUS Astral, MSI Suprim) |
| INFOROM Versions (OEM/EFI/VBIOS) | `nvmlDeviceGetInforomVersion()` | `GPUMetric.inforom_versions_json` + `LatestSnapshot.gpu_inforom_versions_json` | Granular firmware images |
| VBIOS Version | `nvmlDeviceGetVbiosVersion()` | `GPUMetric.vbios_version` / `LatestSnapshot.gpu_vbios_json` | Useful for firmware tracking |
| PCIe Bus ID | `nvmlDeviceGetPciInfo()` → bus_id | `GPUMetric.pci_bus_id` / `LatestSnapshot.gpu_pci_bus_json` | Correlate with `lspci`, identify physical slot |
| Extended PCIe Info (subsystem IDs) | `nvmlDeviceGetPciInfoExt()` | `GPUMetric.pci_subsystem_id` / `LatestSnapshot.gpu_pci_subsystems_json` | AIB vendor:device IDs |
| GPU Serial Number | `nvmlDeviceGetSerial()` | `GPUMetric.serial_number` / `LatestSnapshot.gpu_serials_json` | Unique per physical GPU |
| Board ID | `nvmlDeviceGetBoardId()` | `GPUMetric.board_id` | Manufacturing board identifier |
| GPU Architecture | `nvmlDeviceGetArchitecture()` | `GPUMetric.architecture` | Ampere, Hopper, Ada, etc. |
| Bus Type (PCIe/NVLink) | `nvmlDeviceGetBusType()` | `GPUMetric.bus_type` | PCIe vs NVLink topology |
| BAR1 Memory Info | `nvmlDeviceGetBAR1MemoryInfo()` | `GPUMetric.bar1_memory_mb` | CPU↔GPU aperture size |
| Thermal Settings (thresholds) | `nvmlDeviceGetThermalSettings()` | `GPUMetric.thermal_settings_json` | Slowdown/shutdown temps |
| Dynamic P-States Info | `nvmlDeviceGetDynamicPstatesInfo()` | `GPUMetric.pstates_info_json` | Perf state residency |
| Max Clock Info | `nvmlDeviceGetMaxClockInfo()` | `GPUMetric.max_clocks_json` | Theoretical max clocks |
| Architecture | `nvmlDeviceGetArchitecture()` | `GPUMetric.architecture` | Ampere, Hopper, Ada, etc. |
| ECC Mode/Errors | `nvmlDeviceGetEccMode()`, `nvmlDeviceGetMemoryErrorCounter()` | `GPUMetric.ecc_mode`, `GPUMetric.ecc_errors` | Datacenter GPU reliability |
| Retired Pages | `nvmlDeviceGetRetiredPages()` | `GPUMetric.retired_pages_count` | Memory health indicator |
| Compute Mode | `nvmlDeviceGetComputeMode()` | `GPUMetric.compute_mode` | Default/Exclusive/Prohibited |
| Persistence Mode | `nvmlDeviceGetPersistenceMode()` | `GPUMetric.persistence_mode` | Driver persistence setting |
| MIG Mode (multi-instance GPU) | `nvmlDeviceGetMigMode()` | `GPUMetric.mig_mode` | Datacenter GPU partitioning |

### 2.2 Disk Enhancements (via sysfs/udev)

| Info | Source | Storage Location | Notes |
|------|--------|------------------|-------|
| **Disk Model** | `/sys/block/<disk>/device/model` or `/sys/class/block/<disk>/device/model` | `StorageMetric.model` / `LatestSnapshot.storage_models_json` | Human-readable model (e.g., "Samsung SSD 870 EVO 1TB") |
| **Disk Vendor** | `/sys/block/<disk>/device/vendor` | `StorageMetric.vendor` / `LatestSnapshot.storage_vendors_json` | Manufacturer (e.g., "Samsung", "WD", "Seagate") |
| **Disk Serial** | `/sys/block/<disk>/device/serial` or udev `ID_SERIAL` | `StorageMetric.serial` / `LatestSnapshot.storage_serials_json` | Unique per physical drive |
| **Disk WWN** | `/sys/block/<disk>/device/wwn` or udev `ID_WWN` | `StorageMetric.wwn` | World Wide Name for enterprise tracking |
| **Rotation Rate** | `/sys/block/<disk>/queue/rotational` (0=SSD, 1=HDD) or udev `ID_ATA_ROTATION_RATE_RPM` | `StorageMetric.rotation_rate_rpm` | Distinguish SSD vs HDD |
| **Transport** | `/sys/block/<disk>/device/transport` or udev `ID_BUS` | `StorageMetric.transport` | SATA, NVMe, SAS, USB, etc. |
| **Firmware Version** | `/sys/block/<disk>/device/firmware` or `smartctl -i` | `StorageMetric.firmware` | Drive firmware revision |
| **PHY Speed** | `/sys/class/scsi_device/...` or `smartctl -a` | `StorageMetric.phy_speed_gbps` | Link speed (6Gbps SATA, 16GT/s PCIe, etc.) |

### 2.3 CPU Enhancements

| Info | Source | Storage Location | Notes |
|------|--------|------------------|-------|
| **CPU Vendor** | `cpuinfo.get_cpu_info()['vendor_id_raw']` | `MetricSnapshot.cpu_vendor` / `LatestSnapshot.cpu_vendor` | GenuineIntel, AuthenticAMD |
| **CPU Stepping/Revision** | `cpuinfo` or `/proc/cpuinfo` | `MetricSnapshot.cpu_stepping` | Microcode/revision tracking |
| **Microcode Version** | `/proc/cpuinfo` (microcode field) | `MetricSnapshot.cpu_microcode` | Security patch level |
| **L1/L2/L3 Cache Sizes** | `cpuinfo` or `lscpu` | `MetricSnapshot.cpu_cache_json` | Performance characterization |
| **CPU Flags/Features** | `cpuinfo['flags']` | `MetricSnapshot.cpu_flags_json` | AVX, AVX2, AVX-512, etc. |

### 2.4 System Enhancements

| Info | Source | Storage Location | Notes |
|------|--------|------------------|-------|
| **BIOS Date** | `/sys/class/dmi/id/bios_date` | `LatestSnapshot.motherboard_json.bios_date` | Already have bios_version |
| **Chassis Type** | `/sys/class/dmi/id/chassis_type` | `LatestSnapshot.motherboard_json.chassis_type` | Server, desktop, laptop, etc. |
| **System UUID** | `/sys/class/dmi/id/product_uuid` | `LatestSnapshot.machine_uuid` | Distinct from rig_uuid |
| **Baseboard Asset Tag** | `/sys/class/dmi/id/board_asset_tag` | `LatestSnapshot.motherboard_json.asset_tag` | Inventory tracking |

### 2.5 Network Enhancements

| Info | Source | Storage Location | Notes |
|------|--------|------------------|-------|
| **Interface MAC** | `psutil.net_if_addrs()` (AF_PACKET) | `NetworkMetric.mac_address` / `LatestSnapshot.network_macs_json` | Already have ipv4 |
| **Driver Name** | `ethtool -i <iface>` or `/sys/class/net/<iface>/device/driver/module` | `NetworkMetric.driver` | Identify NIC driver (e.g., igb, mlx5_core) |
| **Driver Version** | `ethtool -i <iface>` | `NetworkMetric.driver_version` | NIC firmware/driver version |

---

## 3. Implementation Plan

### Phase 1: GPU Brand + AIB Board Part Number (+ existing Model Name) — REVISED 2026-09-30

**Scope:** brand + AIB board part number + model name. Model name already
collected end-to-end (`nvmlDeviceGetName` → `GPUMetric.model` +
`LatestSnapshot.gpu_models_json`, shown in the Live Metrics GPU card,
preserved through compaction via `static_fields`). Phase 1 adds **two new
fields**: `gpu_brand` (`nvmlDeviceGetBrand`) + `gpu_board_part_number`
(`nvmlDeviceGetBoardPartNumber` — AIB subvendor marketing name like
"ASUS Astral", "MSI Suprim"). (VBIOS/INFOROM/serial/PCIe bus/board ID/
arch/PCI IDs: researched in `RESEARCH-gpu-static-identifiers.md`,
deferred to Phase 2.)

**Agent Changes (`agent/run.py`, `collect_gpus()`):**
- Brand: `brand_int = pynvml.nvmlDeviceGetBrand(handle)` (verified in
  installed pynvml; returns int from `NVML_BRAND_*`). Map via
  `NVML_BRAND_LABELS` → string label. Wrap in `try/except pynvml.NVMLError`.
- AIB Board Part Number: `gpu_board_part = _nvml_bytes_to_str(
  pynvml.nvmlDeviceGetBoardPartNumber(handle))` — uses existing
  `_nvml_bytes_to_str` helper (bytes→str, None-safe). Wrap in its own
  `try/except pynvml.NVMLError` → `None` on `NOT_SUPPORTED` (rare).
- Add `'gpu_brand': gpu_brand`, `'gpu_board_part_number': gpu_board_part`
  to the per-GPU dict. Bumps: agent 1.11.0, schema 1.16. Windows agent:
  same 10-line change when next touched (backward compatible).

**Server Changes:**
- `GPUMetric`: `gpu_brand` + `gpu_board_part_number` =
  `CharField(max_length=64, blank=True, default='')` — per-row identity,
  survives compaction via `static_fields` (like `model`).
- `LatestSnapshot`: `gpu_brands_json` + `gpu_board_part_numbers_json`
  (arrays, same order as `gpu_models_json`).
- `serializers.py`: in existing GPU loop add both to `GPUMetric` defaults
  and build `gpu_brands` / `gpu_board_part_numbers` lists for `ls_defaults`;
  accept schema `1.16` in `validate_schema_version`.
- `compact_data.py`: add both to `metrics_gpumetric` `static_fields`.
- `dashboard/views.py::_build_gpu_metrics()`: expose both fields.
- **No chart mapping** — both are categorical (same as disk model).
- Live Metrics GPU card: color-coded brand badge + "Board: <part>"
  next to model name (consumer amber / datacenter blue / vGPU purple),
  hidden when blank/Unknown.
- Defense (W001 style): `checks.py::check_gpu_brand_and_part` — fields
  exist on GPUMetric + LatestSnapshot arrays exist, serializer writes them,
  compact `static_fields` includes both.
- Tests: `GpuBrandAndPartTestCase` (3 cases: both written to GPUMetric +
  LatestSnapshot; old-agent payload without new fields → '' defaults;
  view exposes them). Reuse `process_ingest` pattern from disk tests.
- Docs: agent/README (1.11.0/1.16 + collection table rows), architecture
  doc payload example + changelog + appendix enum.

**Migration:** `0055_gpumetric_gpu_brand_board_part_latestsnapshot` —
  four `AddField`s (GPUMetric.brand + board_part + LatestSnapshot arrays),
  nullable/blank defaults, no data loss.

### Phase 2: GPU VBIOS, INFOROM, Serial, PCIe (extended), Board ID, Arch, Bus Type, BAR1, Thermal, P-States, Max Clocks, MIG + PCI IDs (deferred)

**Agent Changes:**
- In `collect_gpus()`: add `nvmlDeviceGetVbiosVersion()`, `nvmlDeviceGetInforomVersion()`,
  `nvmlDeviceGetSerial()`, `nvmlDeviceGetPciInfo()` + `nvmlDeviceGetPciInfoExt()`
  (busId + pciDeviceId/pciSubSystemId → vendor/device/subsystem),
  `nvmlDeviceGetBoardId()`, `nvmlDeviceGetArchitecture()`, `nvmlDeviceGetBusType()`,
  `nvmlDeviceGetBAR1MemoryInfo()`, `nvmlDeviceGetThermalSettings()`,
  `nvmlDeviceGetDynamicPstatesInfo()`, `nvmlDeviceGetMaxClockInfo()`,
  `nvmlDeviceGetMigMode()` + sysfs `/sys/bus/pci/devices/<busId>/
  {vendor,device,subsystem_*}` (see `RESEARCH-gpu-static-identifiers.md` §2b/§2.3d)

**Server Changes:**
- `GPUMetric`: add `vbios_version`, `inforom_versions_json`, `serial_number`,
  `pci_bus_id`, `pci_subsystem_id`, `board_id`, `architecture`, `bus_type`,
  `bar1_memory_mb`, `thermal_settings_json`, `pstates_info_json`,
  `max_clocks_json`, `mig_mode` (all CharField/JSONField, static)
- `LatestSnapshot`: add corresponding JSON arrays
- Serializer + compaction `static_fields` updated; no charts (all identity)
- Rationale + bit-order details: `RESEARCH-gpu-static-identifiers.md`

### Phase 3: Disk Model, Vendor, Serial, WWN — ✅ DONE (branch `feat/disk-hardware-identifiers`, schema 1.15)

**Agent Changes:**
- New helper `_get_disk_model_info(device_name)` in `run.py`
- Read `/sys/block/<disk>/device/{model,vendor,serial,wwn,rotational,transport,firmware}`
- Handle NVMe paths differently (`/sys/block/nvme0n1/device/model` works)
- Add to storage dict in `collect_storage()`

**Server Changes:**
- `StorageMetric`: add `model`, `vendor`, `serial`, `wwn`, `rotation_rate_rpm`, `transport`, `firmware`, `phy_speed_gbps`
- `LatestSnapshot`: add corresponding JSON arrays
- Serializer, compaction (`'model': 'last', 'vendor': 'last', ...`), charts

**Note:** Need to map partition → whole disk (already have `_disk_to_whole_disk()`)

### Phase 4: CPU Vendor, Stepping, Microcode, Cache (Medium Value, Low Effort)

**Agent Changes:**
- In `collect_cpu()`: extend cpuinfo extraction

**Server Changes:**
- `MetricSnapshot`: add `cpu_vendor`, `cpu_stepping`, `cpu_microcode`, `cpu_cache_json` (JSONField)
- `LatestSnapshot`: add same fields
- Serializer, compaction (`'last'` for static fields)

### Phase 5: GPU ECC, Retired Pages, Compute Mode (Lower Value, Medium Effort)

**Agent Changes:**
- In `collect_gpus()`: add ECC, retired pages, compute mode, persistence mode

**Server Changes:**
- `GPUMetric`: add fields
- Serializer, compaction, charts

### Phase 6: Network MAC, Driver (Lower Value, Medium Effort)

**Agent Changes:**
- In `collect_network()`: extract MAC from `psutil.net_if_addrs()`, driver from ethtool

**Server Changes:**
- `NetworkMetric`: add `mac_address`, `driver`, `driver_version`
- `LatestSnapshot`: add JSON arrays
- Serializer, compaction

---

## 4. Schema Version Impact

| Phase | Agent `__schema_version__` | Agent version | Notes | Status |
|-------|----------------------------|---------------|-------|--------|
| (shipped) | 1.14 | 1.9.1 | pre-disk-identifiers | baseline |
| **Phase 3 → done** | **1.15** | **1.10.0** | Disk model/vendor/serial/WWN | ✅ merged `feat/disk-hardware-identifiers` |
| **Phase 1** | **1.16** | 1.11.0 | GPU brand + AIB board part number (+ model) | ⏳ implement next |
| Phase 2 | 1.17 | 1.12.0 | VBIOS, INFOROM, serial, PCIe ext, board ID, arch, bus type, BAR1, thermal, P-states, max clocks, MIG, PCI IDs | deferred |
| Phase 3 | 1.18 | 1.13.0 | CPU vendor, stepping, microcode, cache | deferred |
| Phase 4 | 1.19 | 1.14.0 | GPU ECC, retired pages, compute mode | deferred |
| Phase 5 | 1.20 | 1.15.0 | Network MAC, driver | deferred |

Each phase increments MINOR version (new payload fields, non-breaking).

---

## 5. Defense in Depth (W001/W004/0052)

For each new field added to time-series tables:

1. **Code Fix** — Agent collects, serializer writes, model stores
2. **Django System Check** — Add to `gpu_monitor/metrics_app/checks.py` to verify:
   - Field exists on model
   - Field in `compact_data.py` agg_fields
   - Field in `ChartDataView` metric mapping (if chartable)
3. **Documentation/Skill Update** — Update this plan, architecture docs

### Example Check Addition (for GPU brand):
```python
# In checks.py
if not hasattr(GPUMetric, 'gpu_brand'):
    errors.append(Error('GPUMetric missing gpu_brand field', id='W001'))
# gpu_brand is a STATIC identity field -> lives in static_fields, not agg_fields
gpumetric_cfg = next(c for c in COMPACT_TABLES if c['table'] == 'metrics_gpumetric')
if 'gpu_brand' not in gpumetric_cfg['static_fields']:
    errors.append(Error('gpu_brand missing from compact_data.py static_fields', id='W004'))
```

---

## 6. Priority Recommendation

| Priority | Phase | Rationale | Status |
|----------|-------|-----------|--------|
| ~~P0~~ | ~~Phase 3 (disk)~~ | disk model/vendor/serial/WWN inventory | ✅ done (1.15) |
| **P0 (next)** | **Phase 1 (GPU brand + AIB board part + model)** | brand distinguishes consumer/datacenter/vGPU; AIB part gives subvendor (ASUS Astral); model already collected — tiny change, maximum signal | ⏳ implement next |
| P1 | Phase 2 | VBIOS/INFOROM/serial/PCIe ext/board ID/arch/bus type/BAR1/thermal/P-states/max clocks/MIG/PCI IDs: full GPU identity & health | deferred |
| P2 | Phase 3 | CPU vendor/stepping/microcode: security auditing, perf debugging | deferred |
| P3 | Phase 4 | GPU ECC/retired pages: datacenter-GPU memory health only | deferred |
| P4 | Phase 5 | Network MAC/driver: rarely changes, low operational value | deferred |

---

## 7. Estimated Effort

| Phase | Agent Changes | Server Model Fields | Serializer | Compaction | Charts | Tests | Total |
|-------|---------------|---------------------|------------|------------|--------|-------|-------|
| **1** | ~30 lines | 4 | ~15 lines | 4 entries | 0 (categorical) | 1 check | ~2.5 hr |
| 2 | ~50 lines | 12 | ~25 lines | 12 entries | 0 (identity) | 1 check | ~3.5 hr |
| 3 | ~50 lines | 8 | ~20 lines | 8 entries | 0 (no charts) | 1 check | ~2 hr |
| 4 | ~20 lines | 4 | ~10 lines | 4 entries | 0 | 1 check | ~1 hr |
| 5 | ~30 lines | 4 | ~15 lines | 4 entries | 4 mappings | 1 check | ~1.5 hr |
| 5 | ~25 lines | 3 | ~10 lines | 3 entries | 0 | 1 check | ~1 hr |

**Total: ~8 hours** across 6 phases, can be done incrementally.

## Progress (2026-09-30)
- **Phase 3 (disk model/vendor/serial/WWN): IMPLEMENTED** in branch
  `feat/disk-hardware-identifiers` (agent 1.10.0 / schema 1.15,
  migration 0054, Live Metrics display, W001-style check + 3 tests).
- **Phase 1 (GPU brand + AIB board part number): RESEARCH COMPLETE** —
  see `RESEARCH-gpu-static-identifiers.md` §4a. Both `nvmlDeviceGetBrand`
  and `nvmlDeviceGetBoardPartNumber` verified present + handle-based,
  full brand enum mapping, NOT_SUPPORTED expectations per field, impl
  blueprint (agent 1.11.0 / schema 1.16). Follow-up branch:
  `feat/gpu-brand-model-part`.
- **Phase 2 (VBIOS, INFOROM, serial, PCIe ext, board ID, arch, bus type,
  BAR1, thermal, P-states, max clocks, MIG, PCI IDs): RESEARCH COMPLETE**
  — see `RESEARCH-gpu-static-identifiers.md` §2.2b/§2.3b/§2.3c/§2.3d/§2.3e
  and §4b. All functions verified present in installed pynvml. Deferred
  to Phase 2 branch `feat/agent-gpu-static-identifiers-v2`.

---

## 8. Open Questions for User

1. **Priority order:** Proceed with P0 (GPU brand/VBIOS/serial/PCIe) first, or different order?
2. **Disk model collection:** Use `/sys/block/` directly (simpler) or `udevadm info --query=property` (more complete but requires subprocess)?
3. **GPU brand enum:** Store as integer (NVML_BRAND_*) or string label? String is more readable but integer is compact.
4. **Compaction aggregation:** For static fields like `gpu_brand`, `model`, `vendor` — use `'last'` (most recent non-null). Confirm?
5. **Chart exposure:** Which new fields should be chartable? GPU brand (categorical, not numeric), disk model (categorical) — probably not. Serial/PCIe/bus ID — not chartable. Only numeric fields need chart mappings.

---

## 9. Next Steps (After Approval)

1. Create Phase 1 implementation branch: `feat/agent-gpu-brand-vbios`
2. Implement agent collector changes
3. Create Django migration for `GPUMetric.gpu_brand`, `vbios_version`
4. Update serializer, compaction, charts, checks
5. Test end-to-end with real hardware
6. Repeat for subsequent phases

---

## Appendix: NVML Brand Type Mapping

```python
NVML_BRAND_TYPES = {
    0: 'UNKNOWN',
    1: 'QUADRO',
    2: 'TESLA',
    3: 'NVS',
    4: 'GRID',
    5: 'GEFORCE',
    6: 'TITAN',
    7: 'NVIDIA_VAPPS',
    8: 'NVIDIA_VPC',
    9: 'NVIDIA_VCS',
    10: 'NVIDIA_VWS',
    11: 'NVIDIA_CLOUD_GAMING',
    12: 'QUADRO_RTX',
    13: 'NVIDIA_RTX',
    14: 'NVIDIA',
    15: 'GEFORCE_RTX',
    16: 'TITAN_RTX',
}
```

---

## Appendix: Example Disk Sysfs Paths

```
/sys/block/sda/device/model       → "Samsung SSD 870 EVO 1TB"
/sys/block/sda/device/vendor      → "Samsung"
/sys/block/sda/device/serial      → "S123456789"
/sys/block/sda/device/wwn         → "0x5002538d40123456"
/sys/block/sda/device/transport   → "SATA"
/sys/block/sda/queue/rotational   → "0" (SSD) or "1" (HDD)
/sys/block/nvme0n1/device/model   → "WDC PC SN720 SDAQNTW-512G-1001"
/sys/block/nvme0n1/device/firmware → "123456WD"
```