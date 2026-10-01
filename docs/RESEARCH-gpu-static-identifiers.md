# Research: GPU Static Identifiers via NVML (pynvml)

**Date:** 2026-09-30
**Companion to:** `plan-agent-additional-info-collection.md` (Phases 1–2)
**Status:** Phase 1 **shipped** on `feat/agent-gpu-brand-board-part` —
**`gpu_subvendor` + `gpu_board_part_number`** (NOT `gpu_brand`).
`gpu_brand` was added, then **dropped in the "Phase 1 revised" commit**
(`bd2a5bb`); server migrations `0055` (add brand+part) → `0056` (remove
brand) capture that. See §4a/§4b for the final as-shipped design and the
residual server-side gaps.

---

## 1. Scope

> **SHIPPED FOCUS (Phase 1 revised, 2026-09-30):** the goal is **brand + model
> name**. Model name is already collected end-to-end (`nvmlDeviceGetName` →
> `GPUMetric.model` + `LatestSnapshot.gpu_models_json` → GPU card). The "brand"
> was first implemented as `gpu_brand` (`nvmlDeviceGetBrand`) but was **dropped
> in the "Phase 1 revised" commit** — replaced by **`gpu_subvendor`** (the AIB
> board-partner name, e.g. ASUS/Gigabyte/MSI), derived from the raw PCI
> subsystem hex via `nvmlDeviceGetPciInfo().pciSubSystemId & 0xFFFF` (pynvml
> does NOT expose the subvendor as a string — see §2.3d). **Phase 1 as shipped
> therefore collects two new fields: `gpu_subvendor` + `gpu_board_part_number`
> (the AIB marketing part number).** The rest of the identifiers below are
> researched and deferred to a later phase.
>
> Note on the "brand" wording: for a rig user, "brand" means the AIB partner
> (ASUS/Gigabyte/MSI), not the NVML `NVML_BRAND_*` product line (GeForce/
> Tesla/Quadro). `gpu_subvendor` answers the former; `nvmlDeviceGetBrand`
> (product line) was the mis-target that got reverted.

Feasibility of collecting these GPU static identifiers, all from the
existing `pynvml` dependency (no new packages):

| Info | NVML API | Notes |
|------|----------|-------|
| **Model name** | `nvmlDeviceGetName(handle)` | Already collected end-to-end ✅ |
| **Product-line brand** (GeForce/RTX/Tesla/Quadro) | `nvmlDeviceGetBrand(handle)` | `gpu_brand` — added in Phase 1, **dropped in Phase 1 revised** (see §4b) |
| **AIB Subvendor** (ASUS/Gigabyte/MSI) | `nvmlDeviceGetPciInfo(handle)` → `pciSubSystemId & 0xFFFF` + lookup map | `gpu_subvendor` — Phase 1 ✅ |
| **AIB Board Part Number** | `nvmlDeviceGetBoardPartNumber(handle)` | e.g. "ASUS Astral" — Phase 1 ✅ |
| VBIOS version | `nvmlDeviceGetVbiosVersion(handle)` | Phase 2 |
| INFOROM versions (VBIOS, OEM, EFI, etc.) | `nvmlDeviceGetInforomVersion(handle, object)` | Phase 2 |
| Serial number | `nvmlDeviceGetSerial(handle)` | Phase 2 (NOT_SUPPORTED on consumer) |
| PCIe bus ID | `nvmlDeviceGetPciInfo(handle)` → `.busId` | Phase 2 |
| Extended PCI info (subsystem IDs) | `nvmlDeviceGetPciInfoExt(handle)` | Phase 2 |
| Board ID | `nvmlDeviceGetBoardId(handle)` | Phase 2 (NOT_SUPPORTED on consumer) |
| Architecture (Ampere/Hopper/…) | `nvmlDeviceGetArchitecture(handle)` | Phase 2 |
| Bus type | `nvmlDeviceGetBusType(device)` | Phase 2 (PCIe/NVLink) |
| BAR1 memory info | `nvmlDeviceGetBAR1MemoryInfo(handle)` | Phase 2 |
| Thermal settings (thresholds) | `nvmlDeviceGetThermalSettings(device, sensor)` | Phase 3 (monitoring) |
| Dynamic P-states info | `nvmlDeviceGetDynamicPstatesInfo(device)` | Phase 3 (power mgmt) |
| Max clock info | `nvmlDeviceGetMaxClockInfo(handle, type)` | Phase 3 (performance) |
| Architecture | `nvmlDeviceGetArchitecture(handle)` | Phase 2 |
| Bus type (PCIe/NVLink) | `nvmlDeviceGetBusType(device)` | Phase 2 |
| MIG mode (multi-instance GPU) | `nvmlDeviceGetMigMode(device)` | Phase 3 (datacenter) |

All six take the **device handle** (`nvmlDeviceGetHandleByIndex(i)`) — already
created in the `collect_gpus()` loop, so no extra initialization.

## 2. Verified findings (local introspection of installed pynvml)

Environment: `pynvml` package (deprecated; NVIDIA recommends `nvidia-ml-py`).

### 2.1 All six functions exist in the installed pynvml

```
nvmlDeviceGetBrand        (handle)
nvmlDeviceGetVbiosVersion (handle)
nvmlDeviceGetSerial       (handle)
nvmlDeviceGetPciInfo      (handle)
nvmlDeviceGetBoardId      (handle)
nvmlDeviceGetArchitecture (device)   # same handle
```

### 2.2 Brand enum — full mapping (int → label)

`nvmlDeviceGetBrand()` returns an int from `NVML_BRAND_*`. Observed values in
the installed package (NVML_BRAND_COUNT = 20):

| Value | Constant | Label to store |
|-------|----------|----------------|
| 0 | UNKNOWN | Unknown |
| 1 | QUADRO | Quadro |
| 2 | TESLA | Tesla |
| 3 | NVS | NVS |
| 4 | GRID | Grid |
| 5 | GEFORCE | GeForce |
| 6 | TITAN | Titan |
| 7 | NVIDIA_VAPPS | NVIDIA Virtual Applications |
| 8 | NVIDIA_VPC | NVIDIA Virtual PC |
| 9 | NVIDIA_VCS | NVIDIA vGPU for Compute |
| 10 | NVIDIA_VWS | NVIDIA RTX Virtual Workstation |
| 11 | NVIDIA_CLOUD_GAMING | NVIDIA Cloud Gaming |
| 12 | QUADRO_RTX | Quadro RTX |
| 13 | NVIDIA_RTX | NVIDIA RTX |
| 14 | NVIDIA | NVIDIA |
| 15 | GEFORCE_RTX | GeForce RTX |
| 16 | TITAN_RTX | Titan RTX |
| 17 | NVIDIA_DLA | NVIDIA DLA |
| 18 | NVIDIA_VGAMEDEV | NVIDIA vGameDev |
| 19 | NVIDIA_NPU | NVIDIA NPU |

Recommendation: store the **string label** in the payload (readable in
dashboard/JSON, and immune to future enum renumbering). Unknown future values
→ `'Unknown'` fallback.

### 2.2b AIB Subvendor / Board Part Number — `nvmlDeviceGetBoardPartNumber`

**Signature:** `nvmlDeviceGetBoardPartNumber(handle)` → returns a string
(or bytes on Python 3). This is the **AIB (Add-in-Board) partner's
marketing part number** — e.g. "ASUS Astral", "MSI Suprim", "Gigabyte
Gaming OC", "EVGA FTW3". It is the string printed on the retail box
and the PCB sticker, distinct from the reference design name in
`nvmlDeviceGetName` (e.g. "NVIDIA GeForce RTX 5090").

Availability: present in installed pynvml (`nvmlDeviceGetBoardPartNumber`
exists). Takes the device handle (same as the other calls in the loop).
Returns bytes on Python 3 — must decode via the existing `_nvml_bytes_to_str`
helper. Expected to be universally available on consumer and datacenter
cards alike; if `NOT_SUPPORTED` (rare on very old GPUs), returns `None`.

This is the **subvendor/AIB partner string** the user explicitly asked
for (e.g. "RTX 5090 ASUS Astral" = model "RTX 5090" + board part
"ASUS Astral"). It complements `nvmlDeviceGetName` (reference model)
and `nvmlDeviceGetBrand` (product line).

### 2.3c INFOROM Versions — `nvmlDeviceGetInforomVersion`

**Signature:** `nvmlDeviceGetInforomVersion(handle, infoRomObject)` where
`infoRomObject` is an enum:
- `NVML_INFOROM_OEM` (0) — OEM-specific version
- `NVML_INFOROM_EFI` (1) — EFI version
- `NVML_INFOROM_VBIOS` (2) — VBIOS version (same as GetVbiosVersion?)
- `NVML_INFOROM_MAX` (3)

This gives granular access to the different firmware images on the GPU.
The standard `nvmlDeviceGetVbiosVersion` is likely the VBIOS image;
the OEM and EFI images may contain board-specific firmware revisions
useful for troubleshooting boot/fan-curve issues on specific AIB models.
Returns string (bytes on Python 3), same decoding as VBIOS.

### 2.3d Extended PCIe Info — `nvmlDeviceGetPciInfoExt`

Returns `nvmlPciInfoExt_v1_t` with additional fields:
- `pciSubSystemId` — 32-bit subsystem ID (AIB vendor:device)
- `baseClass` — PCI base class (e.g., 0x03 = Display)
- `subClass` — PCI sub-class (e.g., 0x02 = 3D Controller)
- Same `busId`, `domain`, `bus`, `device`, `pciDeviceId` as v3

This is the **NVML-native way** to get the AIB subsystem IDs without
going through sysfs (Option B in §2b). The `pciSubSystemId` packs
`subsystem_device << 16 | subsystem_vendor` (standard PCI layout).
Extract: `subsys_vendor = pciSubSystemId & 0xFFFF`,
`subsys_device = (pciSubSystemId >> 16) & 0xFFFF`.
Known AIB subsystem vendor IDs:
  `0x1043` = ASUS, `0x1462` = MSI, `0x1458` = Gigabyte,
  `0x3842` = EVGA, `0x10DE` = NVIDIA (reference/FE).

**Phase 1 implementation (as shipped):** both agents collect the subvendor via
the **v3** `nvmlDeviceGetPciInfo(handle)` (NOT `PciInfoExt`) — `pciSubSystemId
& 0xFFFF` → `GPU_SUBVENDOR_MAP` → `gpu_subvendor`. The full map (in
`agent/run.py` and `agent_windows/run.py`) also covers Zotac, EVGA, Palit,
Leadtek, Inno3D, Quanta, InnoVISION, Galax/KFA2, PNY, Gainward, and OEM rigs
(HP/Lenovo/Dell/Samsung/IBM). Unknown IDs → `"Unknown (0xXXXX)"`, never `None`.

```python
GPU_SUBVENDOR_MAP = {
    0x10DE: "NVIDIA (Founders Edition)",
    0x1043: "ASUS", 0x1458: "Gigabyte", 0x1462: "MSI",
    0x19DA: "Zotac", 0x3842: "EVGA", 0x1569: "Palit",
    0x107D: "Leadtek", 0x1E04: "Inno3D", 0x152D: "Quanta",
    0x11A9: "InnoVISION", 0x1B4C: "Galax / KFA2", 0x1ACC: "PNY",
    0x19BE: "Gainward", 0x103C: "HP", 0x17AA: "Lenovo",
    0x1028: "Dell", 0x144D: "Samsung", 0x1014: "IBM", 0x1002: "AMD",
}

subvendor_id = pci_subsystem_id & 0xFFFF   # lower 16 bits = subvendor
gpu_subvendor = GPU_SUBVENDOR_MAP.get(subvendor_id, f"Unknown (0x{subvendor_id:04X})")
```

The `nvmlPciInfo_t` (v3) struct already exposes `pciSubSystemId` on the handle
we own in the `collect_gpus()` loop — no extra init, no sysfs dependency
(Windows agent has no sysfs, so the NVML path is the ONLY cross-platform
option and the one shipped).

### 2.3e Bus Type — `nvmlDeviceGetBusType`

Returns `NVML_BUS_TYPE_PCI` (0) or `NVML_BUS_TYPE_NVLINK` (1).
Useful for identifying NVLink-connected multi-GPU topologies.

### 2.3 Error handling

All six raise `pynvml.NVMLError` subclasses on failure. Relevant classes
confirmed present: `NVMLError_NotSupported`, `NVMLError_InvalidArgument`,
`NVMLError_Uninitialized`, `NVMLError_GpuIsLost`.

**Key expectation from NVML docs:**
- `nvmlDeviceGetSerial`: "For Fermi-class products only." Returns
  `NVML_ERROR_NOT_SUPPORTED` on **many GeForce consumer cards**.
- `nvmlDeviceGetBoardId`: "For Fermi or newer fully supported devices."
  `NVML_ERROR_NOT_SUPPORTED` on consumer GPUs; meaningful mainly on
  multi-GPU/multi-board datacenter systems (devices sharing a boardId are on
  the same PLX bridge).
- `nvmlDeviceGetBrand`: "For all products." Most universally available of
  the six.
- `nvmlDeviceGetVbiosVersion`: "For all products with an inforom." Generally
  available; missing on some vGPU/older drivers.
- `nvmlDeviceGetPciInfo`: "For all products." `.busId` = `domain:bus:device.function`
  (e.g. `0000:01:00.0`). Struct also has `pciDeviceId`/`pciSubSystemId` —
  only `busId` is needed here.
- `nvmlDeviceGetArchitecture`: "For all products." Returns e.g. `"Ampere"`,
  `"Hopper"`, `"Ada"`.

**Implementation consequence:** each field must be collected in its own
`try/except pynvml.NVMLError` so that one `NOT_SUPPORTED` never drops the
others (mirrors the existing per-field pattern already used for
`pcie_*`/clocks in `collect_gpus()`). Failed fields → `None` in the payload;
server defaults to `''`.

### 2.4 Bytes decoding

On Python 3, `nvmlDeviceGetVbiosVersion`, `nvmlDeviceGetSerial`,
`nvmlDeviceGetArchitecture` and `pci_info.busId` return **bytes** — must
decode (existing code already does this for uuid/name:
`raw_uuid.decode('utf-8')`). `nvmlDeviceGetBrand` and `nvmlDeviceGetBoardId`
return plain ints.

## 2b. GPU VENDOR — where it comes from (added 2026-09-30)

"GPU vendor" is the PCI vendor of the device. Two ways to get it, both
already reachable from `collect_gpus()`:

### Option A — sysfs by PCI bus ID (RECOMMENDED)
1. Get `busId` from `nvmlDeviceGetPciInfo(handle)` (already planned, §4).
   Format `domain:bus:device.function`, e.g. `0000:01:00.0`.
2. Read plain sysfs (no sudo, no new deps — same pattern as the disk
   identifiers just shipped):
   - `/sys/bus/pci/devices/<busId>/vendor`  -> `0x10de`  (GPU vendor ID)
   - `/sys/bus/pci/devices/<busId>/device`  -> e.g. `0x2204` (GPU device ID)
   - `/sys/bus/pci/devices/<busId>/class`   -> e.g. `0x030200` (3D controller)
   - `/sys/bus/pci/devices/<busId>/subsystem_vendor` / `subsystem_device`
     -> AIB board variant (identifies the add-in-board partner/rebrand)

Why recommended: exact vendor/device IDs with **no bit-order
ambiguity** (the NVML `pciDeviceId` packs them into one 32-bit int —
see Option B), cross-verifiable against `lspci`, and reuses the
`_read_sysfs()` helper already added for disks. Fallback-safe: if the
`busId` device node is missing (vGPU guest), return `None`.

### Option B — NVML `pciDeviceId` / `pciSubSystemId`
`nvmlPciInfo_t` (v3) fields, all from the one handle:
```
domain            c_uint   PCI domain (0x00000000 .. 0xffffffff)
bus               c_uint   0..0xff
device            c_uint   0..31
busId             char[64] 'domain:bus:device.function'
pciDeviceId       c_uint   'combined 16-bit device id and 16-bit vendor id'
pciSubSystemId    c_uint   32-bit Sub System Device ID
```
`pciDeviceId` is the 32-bit PCI ID word `(device_id << 16) | vendor_id`
(standard PCI header layout, device id in high 16 bits, vendor id in
low 16 bits). NVIDIA's PCI **vendor ID = `0x10DE`** (legacy NVIDIA/ST
IDs `0x104A` and `0x12D2` also exist for very old parts). To extract:
    vendor = pciDeviceId & 0xFFFF            # -> 0x10DE
    device = (pciDeviceId >> 16) & 0xFFFF    # -> e.g. 0x2204

### Interpretation for this fleet
- Because the agent is **NVIDIA-only** (pynvml), `vendor` will be
  `0x10DE` for essentially every modern rig. Its value is (a) a
  stable hardware fingerprint independent of the marketing `model`
  string, and (b) future-proofing if a non-NVIDIA path is ever added.
- The user-facing "is it NVIDIA / which product line" question is
  better answered by **brand** (`nvmlDeviceGetBrand`) + **model name**
  (already collected) than by the raw vendor ID. The AIB partner
  (e.g. Gigabyte/ASUS/MSI rebrand) shows up in `subsystem_vendor`/`device`
  and sometimes in the `model` string.
- Device ID -> chip mapping is public (e.g. `0x2204` = GA102
  RTX 3090, `0x2206` = GA102 RTX 3090 Ti). No need to ship a lookup
  table; store the raw IDs and let the UI/report annotate later.

### Recommendation
Add to the planned payload (Option A): `gpu_vendor` (`0x10de`),
`gpu_device_id` (`0x2204`), `gpu_pci_subsystem` (`subsys_vendor:subsys_device`),
alongside `pci_bus_id`. All four from sysfs given `busId`; each in its
own try/except. Display in the Live Metrics GPU card as a compact
`PCI: 0000:01:00.0 · 10de:2204` line.

## 3. Value assessment (for this rig-monitoring use case)

| Field | Value for a GPU rig fleet | Notes | Status |
|-------|---------------------------|-------|--------|
| **Model name** | **High** | Already collected end-to-end (`nvmlDeviceGetName` → `GPUMetric.model` + `LatestSnapshot.gpu_models_json` → GPU card). Nothing to do — this is half of the "brand + model" goal. | ✅ collected today |
| **Product-line brand** (`gpu_brand`) | **High** | One-line distinction GeForce vs Tesla vs Quadro — consumer vs datacenter card; good for fleet grouping. **Shipped in `4645286`, dropped in `bd2a5bb` (Phase 1 revised)** — superseded by `gpu_subvendor` (AIB partner), which answers the user's actual "brand" question. | ✖ reverted (Phase 1 revised) |
| **VBIOS** | **High** | Firmware-level bug tracking ("X model broke on VBIOS 95.02.xx"); also a second stable hardware fingerprint alongside UUID. | deferred (Phase 2) |
| **PCIe bus ID** | **High** | Correlates GPU to physical slot / `lspci` output; stable even when NVMe/USB renumber; useful for "GPU fell off the bus" diagnostics. | deferred (Phase 2) |
| **Architecture** | **Medium-High** | Ampere/Hopper/Ada grouping; useful for benchmark comparisons and per-arch alerts. | deferred (Phase 2) |
| **Serial** | **Medium** | Unique per physical card — enables RMA/warranty tracking. BUT: frequently `NOT_SUPPORTED` on GeForce consumer cards (verified expectation from NVML docs), so expect empty values on most consumer rigs. | deferred (Phase 2) |
| **Board ID** | **Low-Medium** | Only meaningful on multi-GPU boards/PLX systems; meaningless on standard desktop 1-card-per-slot rigs. Cheap to collect anyway. | deferred (Phase 2) |
| **PCI Vendor / Device ID** | **Medium** | `0x10de:0x2204`-style fingerprint — stable, independent of marketing names; vendor is almost always `0x10DE` (NVIDIA-only agent). Device ID distinguishes chip (GA102 vs GA104). See §2b. | deferred (Phase 2) |
| **AIB Subvendor name** | **High** | Identifies add-in-board partner/rebrand (Gigabyte/ASUS/MSI) — human-readable "brand" answer for support/warranty. `gpu_subvendor` from `pciSubSystemId & 0xFFFF`. | ✅ Phase 1 (shipped) |
| **AIB Board Part Number** | **High** | Human-readable AIB partner marketing name (e.g. "ASUS Astral", "MSI Suprim") — `nvmlDeviceGetBoardPartNumber`. Directly answers "which card?" for support/warranty. Persisted server-side (GPUMetric + LatestSnapshot). | ✅ Phase 1 (shipped) |
| **INFOROM versions** (OEM/EFI/VBIOS) | **Medium** | Granular firmware revisions per image — useful for debugging boot/fan issues on specific AIB models. | Phase 2 |
| **PCI subsystem IDs** (AIB vendor:device) | **Medium** | Machine-readable AIB partner ID via `pciSubSystemId` — cross-checkable with PCI ID databases. | Phase 2 |
| **Bus type** (PCIe/NVLink) | **Low** | Distinguishes PCIe vs NVLink topologies for multi-GPU. | Phase 2 |
| **BAR1 memory** | **Low** | Aperture size for CPU↔GPU memory mapping; relevant for pinned memory workloads. | Phase 3 |
| **Thermal settings** (thresholds) | **Medium** | Slowdown/shutdown temps per sensor — useful for thermal policy auditing. | Phase 3 |
| **Dynamic P-states** | **Medium** | Perf state residency + transition counts — power/performance correlation. | Phase 3 |
| **Max clock info** | **Low** | Theoretical max clocks per domain (graphics, SM, memory, video). | Phase 3 |
| **MIG mode** | **Low** | Multi-Instance GPU partitioning — datacenter only. | Phase 3 |

## 4. Recommended implementation (focus: brand + model name)

### 4a. AS SHIPPED — AIB Subvendor + AIB Board Part Number (model already collected)

> **Revision note:** this section originally specified `gpu_brand`
> (`nvmlDeviceGetBrand`, the `NVML_BRAND_*` product line) + board part.
> `gpu_brand` was added in `4645286` then **removed in `bd2a5bb`** ("Phase 1
> revised"), leaving **`gpu_subvendor` + `gpu_board_part_number`** as the two
> Phase 1 fields. The server migrations `0055` → `0056` reflect that (add
> brand+part, then remove brand). What follows is the design that actually
> shipped, mirrored 1:1 in both `agent/run.py` and `agent_windows/run.py`.

**Agent, in `collect_gpus()` per-GPU loop, after clocks:**

```python
# 1) AIB subvendor from the raw PCI subsystem ID (lower 16 bits = subvendor)
gpu_subvendor = None
try:
    pci_info = pynvml.nvmlDeviceGetPciInfo(handle)
    if pci_info and hasattr(pci_info, 'pciSubSystemId'):
        gpu_subvendor = _get_gpu_subvendor_name(pci_info.pciSubSystemId)
except pynvml.NVMLError:
    pass  # PCI info not available

# 2) AIB marketing board part number (e.g. "ASUS Astral", "MSI Suprim")
gpu_board_part = None
try:
    gpu_board_part = _nvml_bytes_to_str(pynvml.nvmlDeviceGetBoardPartNumber(handle))
except pynvml.NVMLError_NotSupported:
    pass  # NOT_SUPPORTED on some GPUs
except pynvml.NVMLError:
    pass  # Other NVML errors
```

`_get_gpu_subvendor_name(pci_subsystem_id)` returns
`GPU_SUBVENDOR_MAP.get(pci_subsystem_id & 0xFFFF, "Unknown (0xXXXX)")`
(full map §2.3d). `_nvml_bytes_to_str()` is the shared bytes→str helper
(None-safe, strip). Add `'gpu_subvendor': gpu_subvendor` and
`'gpu_board_part_number': gpu_board_part` to the per-GPU dict. The existing
`model` (from `nvmlDeviceGetName`) is already in the dict, serializer, models,
compaction `static_fields`, and the GPU card.

**Why `gpu_board_part_number` lives in `GPUMetric` (timeseries) AND
`LatestSnapshot`** — same as `model`: the AIB part number is a stable per-GPU
identity; storing it per row lets us answer "which ASUS Astral cards ran in the
past 31 days?" after compaction. It is display-only (no chart mapping —
categorical, like disk model).

**`gpu_subvendor` — collected, not persisted (Phase 1 gap).** The agent emits
`gpu_subvendor` in the payload on every snapshot, but the **server does NOT
store it**: `GPUMetric` has no `gpu_subvendor` column and the serializer does
not read it (only `gpu_board_part_number` is persisted, plus the
`gpu_board_part_numbers_json` summary array on `LatestSnapshot`). It is
currently transport-only — a candidate for Phase 2 server storage if we want
subvendor in the GPU card / fleet overview. **Status 2026-10-01:** still
true — see §6 (why it is invisible on Live Metrics and the full fix plan).

### 4b. DEFERRED (later phase) — the remaining keys
The full blueprint (gpu_vbios, gpu_inforom_versions, gpu_serial,
pci_bus_id, board_id, gpu_architecture, gpu_vendor, gpu_device_id,
gpu_pci_subsystem) with the
`_nvml_bytes_to_str()` helper, per-field `try/except pynvml.NVMLError`, and
sysfs PCI-ID reads (§2b) remains the design for the follow-up phase. Each
field follows the same pattern:

```python
gpu_vbios = None
try:
    gpu_vbios = _nvml_bytes_to_str(pynvml.nvmlDeviceGetVbiosVersion(handle))
except pynvml.NVMLError:
    pass
```

Immediate payload keys per GPU entry (as shipped): `gpu_subvendor`,
`gpu_board_part_number` (both nullable; `gpu_subvendor` is agent-side only).
(deferred, later phase: `gpu_vbios`, `gpu_inforom_versions`, `gpu_serial`,
`pci_bus_id`, `pci_info_ext`, `board_id`, `gpu_architecture`,
`gpu_vendor`, `gpu_device_id`, `gpu_pci_subsystem`, `gpu_bus_type`,
`gpu_bar1_memory`, `gpu_thermal_settings`, `gpu_pstates_info`,
`gpu_max_clocks`, `gpu_mig_mode` — all nullable.)

---

## 6. Why `gpu_subvendor` / `gpu_board_part_number` are NOT visible on Live Metrics — root cause + fix (verified 2026-10-01, static analysis + live payload on `feat/agent-gpu-brand-board-part` @ `ffa3528`)

> **Agent side is confirmed DONE (live payload, 2026-10-01).** The Windows
> agent (RTX 3060, `GPU-a322…38aa`) already sends both keys:
> `"gpu_subvendor": "MSI"`, `"gpu_board_part_number": null` — i.e. it emits
> schema-1.17 fields and the server accepts `1.17`. **All remaining work is
> server-side**: ingest passes the keys through untouched, but the
> serializer drops `gpu_subvendor` and the board-part value arrives as
> `null` → hidden. No agent change is needed for either field.

### 6.1 Full pipeline audit (static)

| Step | `gpu_board_part_number` | `gpu_subvendor` |
|------|-------------------------|-----------------|
| Agent payload (Linux `agent/run.py:769-774`, Windows `agent_windows/run.py:927-931`) | ✅ emitted | ✅ emitted |
| Ingest serializer (`metrics_app/serializers.py:161` GPUMetric default; `:185` + `:502` `gpu_board_part_numbers_json`) | ✅ read + persisted | ❌ **never read** |
| Model (`metrics_app/models.py:76` `GPUMetric.gpu_board_part_number`; `:269` `LatestSnapshot.gpu_board_part_numbers_json`) | ✅ both columns exist (migrations `0055`/`0056`, applied) | ❌ no column anywhere |
| View (`dashboard/views.py:201` in `_build_gpu_metrics`, single builder shared by `rig_detail` + `htmx_metrics`) | ✅ key exposed | ❌ key never built |
| Template (`_metrics_cards.html:396-400`, GPU card, renders only when value truthy) | ✅ line exists | ❌ nothing to render |

**Conclusion: the `gpu_board_part_number` pipeline is complete and correct in
code.** Both render paths (initial page load and the 30s HTMX poll) funnel
through the same `_build_gpu_metrics()`, so there is no second builder that
skips the field. The template's `{% if %}` is the only silent no-op: an empty
string simply shows no "Board:" line.

### 6.2 Root cause — exactly what the server does (and does not do)

1. **`gpu_subvendor` — dropped entirely on the server side (the real bug).**
   It arrives in the payload (verified above), but:
   - `IngestSerializer.metrics` is a plain `JSONField`
     (`metrics_app/serializers.py:25`) and `IngestView.post`
     (`metrics_app/views.py:44-117`) passes it through — **no change needed
     at the API layer**; `process_ingest` receives the key via
     `gpu_list = metrics_data.get('gpus', [])` (`serializers.py:65`).
   - `process_ingest` **never reads `gpu_subvendor`** — it is not in the
     `GPUMetric` `defaults` (`serializers.py:158-179`) and not appended to
     any `LatestSnapshot` summary array (`serializers.py:181-201`,
     `:498-518`).
   - No model column exists (`GPUMetric.gpu_subvendor`,
     `LatestSnapshot.gpu_subvendors_json` are absent).
   - `_build_gpu_metrics` (`dashboard/views.py:181-219`) never builds the
     key, so the GPU-card dict on Live Metrics has no subvendor.
   → **It cannot display today, and never will — until §6.3 items 1-5 land.**

2. **`gpu_board_part_number` — pipeline complete; `null` is correctly hidden.**
   The serializer writes `gpu.get('gpu_board_part_number', '')` into
   `GPUMetric` (`serializers.py:161`; field is `null=True`, so `None` stores
   as NULL) and `gpu.get('gpu_board_part_number') or ''` into
   `LatestSnapshot` (`:185`); the view exposes it
   (`dashboard/views.py:201`); the template renders the "Board:" line only
   when truthy (`_metrics_cards.html:396-400`). A payload value of `null`
   therefore yields **no** "Board:" line by design:
   - The MSI RTX 3060 above reports `null` because NVML
     `nvmlDeviceGetBoardPartNumber` returns nothing for it (consumer card
     without a marketing board-part string) — a legitimate value, not a bug.
   - The 2026-09-30 dev-DB snapshot (1086 empty `GPUMetric` rows, rig
     `6746…817d` on `1.10.0-win`) pre-dated the agent update; the live
     payload confirms the current agent now sends the field.
   - Any card that *does* have a board-part string will display it with
     **zero** further server-side code.

**Summary: subvendor = 5 server fragments to add (§6.3); board-part = no
display code to change (only add it to compaction `static_fields`, §6.3
item 6).**

### 6.3 Exact code fragments to change (follow-up branch `feat/gpu-subvendor-display`, server-only)

**0. Ingest / API layer — NO changes.** `IngestView.post`
(`metrics_app/views.py:44-117`) and `IngestSerializer`
(`metrics_app/serializers.py:19-35`) pass `metrics` through as `JSONField`;
both keys already reach `process_ingest` via
`gpu_list = metrics_data.get('gpus', [])` (`serializers.py:65`).
`schema_version` 1.17 is already accepted (`serializers.py:33`). (Nit: that
accepted-version tuple contains a duplicate `'1.16'` token — harmless; clean
up when that line is touched.)

**1. `metrics_app/models.py` — two new fields**
- `GPUMetric`, next to `gpu_board_part_number` (`models.py:76`):
```python
gpu_subvendor = models.CharField(max_length=64, blank=True, default='', null=True)  # AIB partner: MSI/ASUS/Gigabyte/…
```
- `LatestSnapshot`, next to `gpu_board_part_numbers_json` (`models.py:269`):
```python
gpu_subvendors_json = models.JSONField(default=list, blank=True)  # ["MSI", ""]
```

**2. New migration `metrics_app/migrations/0057_gpumetric_gpu_subvendor.py`**
```python
dependencies = [('metrics_app', '0056_remove_gpu_brands_json')]
operations = [
    migrations.AddField(model_name='gpumetric', name='gpu_subvendor',
        field=models.CharField(blank=True, default='', max_length=64, null=True)),
    migrations.AddField(model_name='latestsnapshot', name='gpu_subvendors_json',
        field=models.JSONField(blank=True, default=list)),
]
```
Additive only (existing rows → `''`/`[]`); `makemigrations --check` must
come back clean after item 1.

**3. `metrics_app/serializers.py` — `process_ingest`, four spots**
- List init, next to `gpu_board_part_numbers = []` (`:136`):
```python
gpu_subvendors = []
```
- `GPUMetric.objects.update_or_create(... defaults={...})`, next to
  `'gpu_board_part_number'` (`:161`):
```python
'gpu_subvendor': gpu.get('gpu_subvendor', ''),
```
- Summary-array loop, next to `:185`:
```python
gpu_subvendors.append(gpu.get('gpu_subvendor') or '')
```
- `LatestSnapshot` defaults, next to `'gpu_board_part_numbers_json'` (`:502`):
```python
'gpu_subvendors_json': gpu_subvendors,
```
Older payloads without the key → `''`/`[]` — backward-compatible by
construction (`or ''` also normalizes JSON `null`).

**4. `dashboard/views.py` — `_build_gpu_metrics`, next to `:201`**
```python
'gpu_subvendor': _json_get(snapshot.gpu_subvendors_json, i, ''),
```
No other view change: `rig_detail` (`:572`) and `htmx_metrics` (`:587`) both
route through `_fetch_rig_metrics` → `_build_gpu_metrics` (single builder;
the 50 s `LatestSnapshot` cache needs no key change — same object shape).

**5. Template `dashboard/_metrics_cards.html` — after the Board block (`:396-400`)**
```html
{# AIB Subvendor (e.g., MSI, ASUS, Gigabyte) #}
{% if gpu.gpu_subvendor %}
<div class="text-xs text-gray-500 mb-1">
    Subvendor: <span class="text-gray-300 font-mono">{{ gpu.gpu_subvendor }}</span>
</div>
{% endif %}
```

**6. Compaction `metrics_app/management/commands/compact_data.py` (table
`metrics_gpumetric`, `:72`) — closes gap 2 in the same branch:**
```python
'static_fields': ['model', 'gpu_uuid', 'snapshot_id', 'gpu_board_part_number', 'gpu_subvendor'],
```
Without this BOTH statics are lost at tier-2/3 compaction (W001/W004 bug
class — `model`/`gpu_uuid` survive today, these two would not).

**7. Defense in depth (project rule: code + check + test + docs)**
- `metrics_app/checks.py`: add a W001-style system check that every
  `GPUMetric` field written in the serializer `defaults` is covered by
  compaction `agg_fields` ∪ `static_fields` (extend the existing
  `check_gpu_uuid_compaction_defense` pattern).
- `metrics_app/tests.py`: serializer unit test — a 1.17 payload with
  `gpu_subvendor: "MSI"` + `gpu_board_part_number: null` must yield
  `GPUMetric.gpu_subvendor == "MSI"`,
  `LatestSnapshot.gpu_subvendors_json == ["MSI"]`, and board part stored as
  `None`/`''`.
- Docs sync: agent READMEs — close gap 1 ("subvendor transport-only" is no
  longer true after this branch) and gap 3 (version/field-table drift;
  Windows table should advertise subvendor + board part, not "brand").

### 6.4 Deploy & verify

1. `python3 manage.py makemigrations metrics_app` → must produce exactly
   `0057_gpumetric_gpu_subvendor`; then `python3 manage.py check` (W001
   check green).
2. `python3 manage.py test metrics_app dashboard` (new serializer test
   green; existing `_build_gpu_metrics` tests in
   `dashboard/tests.py:1151+` still pass — they tolerate extra keys).
3. Deploy: apply `0057`, restart gunicorn. No `collectstatic` needed
   (template change, no static files).
4. Within ~60 s of the next heartbeat:
   ```sql
   SELECT agent_version, gpu_subvendors_json, gpu_board_part_numbers_json
   FROM metrics_latest_snapshot
   WHERE rig_uuid = '67462048-dbd9-42bc-9987-d3b48d0a817d';
   -- expected: 1.17-era agent, ["MSI"], [""]
   ```
5. Live Metrics → rig → GPU card: **"Subvendor: MSI"** visible; "Board:"
   line stays hidden (correct — NVML returned `null` for that card).
6. Regression guard: a rig still on an older agent (no `gpu_subvendor`
   key) ingests fine, shows no Subvendor line, and produces no 500s.

**Bumps (as shipped):** Phase 1: `__version__` 1.11.0 /
`__schema_version__` 1.16 (`4645286`). "Phase 1 revised" (`bd2a5bb`) kept
1.11.0/1.16 (field rename only, payload-compatible). Final cleanup
(`ac340f2`, which also added `GPU_SUBVENDOR_MAP` + the subvendor
collection) bumped Linux to **1.12.0 / 1.17** — the Windows agent was
bumped to schema 1.17 in the same commit but its version line stayed
`1.10.0-win` (see gap 3 below).

**Server (as shipped — AIB board part number persisted; subvendor transport-only):**
- `GPUMetric.gpu_board_part_number`: `CharField(max_length=128,
  blank=True, default='', null=True)` — per-row identity.
- `LatestSnapshot.gpu_board_part_numbers_json`: JSON array, same order as
  `gpu_models_json`.
- `serializers.py`: adds `gpu_board_part_number` to `GPUMetric` defaults
  and builds the `gpu_board_part_numbers` list. The payload's
  `gpu_subvendor` key is currently **ignored** (no model field).
- `dashboard/views.py::_build_gpu_metrics()`: exposes
  `gpu_board_part_number` via
  `_json_get(snapshot.gpu_board_part_numbers_json, i)`.
- Migrations: `0055_gpumetric_gpu_brand_board_part` (4 AddFields: brand +
  board part on GPUMetric, brand + board-part arrays on LatestSnapshot)
  → `0056_remove_gpu_brands_json` (removes `gpu_brand` +
  `gpu_brands_json`).
- `validate_schema_version` accepts up to `1.17`.

**Known server-side gaps after Phase 1 (fix before relying on the data):**
1. `gpu_subvendor` is not persisted anywhere (no `GPUMetric` column, no
   `LatestSnapshot` array, serializer ignores it) — transport-only today.
   **Visible consequence on Live Metrics: subvendor can never render in the
   GPU card (no data source). Fix: §6.3 (items 1-5).**
2. `gpu_board_part_number` is **not** in `compact_data.py`
   `static_fields` (currently `['model', 'gpu_uuid', 'snapshot_id']`) →
   it is lost at tier-2/3 compaction (W001/W004 defense class). `model`
   and `gpu_uuid` are the only statics kept.
3. Agent READMEs are out of sync: Linux README says `1.10.0 / 1.15`
   (agent is `1.12.0 / 1.17`); Windows README says `1.10.0-win / 1.16`
   (payload now includes the `gpu_subvendor` field → schema 1.17) and its
   GPU table row still advertises "**brand**".
4. `checks.py` has no W001-style check for board part / subvendor
   (only `check_gpu_uuid_compaction_defense`).

**Schema versioning:** 1.14 → **1.15** (disk identifiers, shipped in
`feat/disk-hardware-identifiers`, agent 1.10.0) → **1.16** (GPU AIB board
part number, agent 1.11.0) → **1.17** (GPU AIB subvendor via
`pciSubSystemId`, agent 1.12.0, `ac340f2`) → 1.18+ (deferred: VBIOS,
INFOROM, PCIe ext, PCI IDs, serial, board ID, arch, bus type, BAR1,
thermal, P-states, max clocks, MIG).
Server must accept 1.15, 1.16, 1.17, and later.

**Edge cases / pitfalls:**
1. `NOT_SUPPORTED` is normal on consumer cards for serial/board — must not
   log a warning per field (silent None, same as existing `pcie_*` handling).
2. `board_id` int 0 is a valid value — use `None` sentinel for "absent",
   never falsy-check the int.
3. `busId` format can be `0000:xx:xx.x` (4-digit domain) on newer kernels —
   store as string verbatim, max_length 32 is plenty.
4. vGPU guests: brand may report `Grid`/`NVIDIA RTX` (vWS) instead of the
   physical product — acceptable, reflects what the agent sees.
5. Do NOT move `nvmlInit()` — reuse the handle already in the loop; do not
   add a second init/shutdown.
6. pynvml is deprecated in favor of `nvidia-ml-py` — the API surface used
   here is identical in both, so no migration risk.

## 5. Effort estimate

**Immediate (AIB subvendor + board part number; model already collected):**
~45 min agent (two calls + subvendor map + bump), ~45 min server
(GPUMetric 1 field + LatestSnapshot 1 array + serializer + view +
migrations; subvendor storage NOT implemented — see gaps), ~30 min
docs/tests. Total ≈ 2 h, single branch
`feat/agent-gpu-brand-board-part` (shipped 2026-09-30; the original
`gpu_brand` work was added then reverted on the same branch).

**Deferred (12+ fields):** ~1.5 h agent, ~1.5 h server,
~45 min docs/tests ≈ 3.5 h on a follow-up branch.
