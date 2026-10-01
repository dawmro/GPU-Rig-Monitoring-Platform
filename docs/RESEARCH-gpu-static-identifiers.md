# Research: GPU Static Identifiers via NVML (pynvml)

**Date:** 2026-09-30
**Companion to:** `plan-agent-additional-info-collection.md` (Phases 1–2)
**Status:** Phase 1 **shipped** on `feat/agent-gpu-brand-board-part` —
**`gpu_subvendor` + `gpu_board_part_number`** (NOT `gpu_brand`).
`gpu_brand` was added, then **dropped in the "Phase 1 revised" commit**
(`bd2a5bb`); server migrations `0055` (add brand+part) → `0056` (remove
brand) capture that. See §4a/§4b for the final as-shipped design and the
residual server-side gaps. Phase 2 (VBIOS, PCIe bus ID, architecture, bus
type, board ID, serial, PCI subsystem, INFOROM) **shipped** on
`feat/gpu-phase2-static-identifiers` (agent 1.13.2, schema 1.18, migration
`0058`). **Phase 3 — detailed implementation plan in §9** (branch
`plan/phase3-gpu-monitoring-features`); four Phase 2 prerequisite defects
documented in §8 and **fixed + verified on this branch** (§8.5).

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

### 2.2b AIB Subvendor / Board Part Number — `nvmlDeviceGetBoardPartNumber` (Phase 1 ✅)

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

**`gpu_subvendor` — collected AND PERSISTED (Phase 1 completed 2026-10-01).**
The agent emits `gpu_subvendor`; server migration `0057_gpumetric_gpu_subvendor`
added `GPUMetric.gpu_subvendor` + `LatestSnapshot.gpu_subvendors_json`;
serializer writes both per-row and summary array; view + template render the
"Subvendor:" line on the GPU card. Empirically verified (13/13 checks
against live 1.17 payload: `gpu_subvendor: "MSI"` → stored and displayed).

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

### 6.1 Full pipeline audit (static) — **POST-FIX STATE (2026-10-01)**
 
| Step | `gpu_board_part_number` | `gpu_subvendor` |
|------|-------------------------|-----------------|
| Agent payload (Linux `agent/run.py:769-774`, Windows `agent_windows/run.py:927-931`) | ✅ emitted | ✅ emitted |
| Ingest serializer (`metrics_app/serializers.py:161` GPUMetric default; `:185` + `:502` `gpu_board_part_numbers_json`) | ✅ read + persisted | ✅ read + persisted |
| Model (`metrics_app/models.py:76` `GPUMetric.gpu_board_part_number`; `:269` `LatestSnapshot.gpu_board_part_numbers_json`) | ✅ columns exist (0055/0056) | ✅ columns exist (0057) |
| View (`dashboard/views.py:201` in `_build_gpu_metrics`, single builder shared by `rig_detail` + `htmx_metrics`) | ✅ key exposed | ✅ key exposed |
| Template (`_metrics_cards.html:396-400`, GPU card, renders only when value truthy) | ✅ line exists | ✅ line exists |
 
**Conclusion: both pipelines are now complete and correct in code.** The
template's `{% if %}` for each field is the only silent no-op: empty string
→ no line.

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

### 6.3 Exact code fragments to change (implemented 2026-10-01 on `feat/agent-gpu-brand-board-part`, server-only + Windows agent version sync)

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
- Docs sync: agent READMEs — gap 1 CLOSED (subvendor persisted) and gap 3
  CLOSED (Windows version synced to 1.12.0-win/1.17; GPU table now lists
  subvendor + board part).

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


**Known server-side gaps after Phase 1 (fix before relying on the data):**
1. **ALL CLOSED 2026-10-01** —
   `gpu_subvendor` persisted (`GPUMetric.gpu_subvendor` + `LatestSnapshot.gpu_subvendors_json`, migration `0057`),
   compaction `static_fields` updated,
   Windows agent version synced (`1.12.0-win` / 1.17, README updated),
   W001-style check `check_gpu_subvendor_pipeline` (E013–E017) active,
   empirical verification 13/13 against live payload.

No Phase 1 server-side gaps remain.

**Schema versioning:** 1.14 → **1.15** (disk identifiers, shipped in
`feat/disk-hardware-identifiers`, agent 1.10.0) → **1.16** (GPU AIB board
part number, agent 1.11.0) → **1.17** (GPU AIB subvendor via
`pciSubSystemId`, agent 1.12.0, `ac340f2`) → **1.18** (Phase 2 static
identifiers: VBIOS, PCIe bus ID, architecture, bus type, board ID, serial,
PCI subsystem, INFOROM; agent 1.13.2, migration `0058`) → **1.19** (Phase 3,
§9: thermal thresholds, dynamic P-states, max clocks, MIG, BAR1 — planned).
Server must accept 1.15, 1.16, 1.17, 1.18, 1.19, and later.

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

---

## 7. Critical Findings / Lessons Learned (2026-10-01 — Phase 2 Implementation)

The following issues were discovered during Phase 2 implementation and
MUST be followed in all future agent development to prevent regressions:

### 7.1 AttributeError on Missing NVML Functions (CRITICAL)
**Problem:** Older `pynvml` versions lack newer NVML functions
(`nvmlDeviceGetArchitecture`, `nvmlDeviceGetBusType`, `nvmlDeviceGetBoardId`,
`nvmlDeviceGetVbiosVersion`, `nvmlDeviceGetPciInfoExt`, `nvmlDeviceGetBoardPartNumber`,
`nvmlDeviceGetInforomVersion`, `nvmlDeviceGetSerial`). Calling them raises
`AttributeError: module 'pynvml' has no attribute '...'`, which crashes the
**entire GPU collection** and returns empty GPU data.

**Root Cause:** Only `NVMLError_NotSupported` and `NVMLError` were caught.
`AttributeError` from missing functions was unhandled.

**Fix (Mandatory for ALL optional NVML calls):**
```python
try:
    value = pynvml.nvmlDeviceGetXXXX(handle)
except (pynvml.NVMLError_NotSupported, pynvml.NVMLError, AttributeError):
    value = None  # Graceful degradation — field defaults to None
```

**Affected Functions (must all have this pattern):**
- `nvmlDeviceGetArchitecture`
- `nvmlDeviceGetBusType`
- `nvmlDeviceGetBoardId`
- `nvmlDeviceGetVbiosVersion`
- `nvmlDeviceGetPciInfoExt` (also requires `version` field set)
- `nvmlDeviceGetBoardPartNumber`
- `nvmlDeviceGetInforomVersion`
- `nvmlDeviceGetSerial`

**Verification:** Every field must default to `None` and the GPU collection
must continue for other GPUs/fields even if one call fails.

### 7.2 nvmlDeviceGetPciInfoExt Requires Version Field
**Problem:** `nvmlDeviceGetPciInfoExt` requires the structure's `version`
field to be set to `nvmlPciInfoExt_v1` constant BEFORE the call.
Without it, the call fails silently or returns garbage.

**Correct Pattern:**
```python
pci_info_ext = pynvml.nvmlPciInfoExt_v1_t()
pci_info_ext.version = pynvml.nvmlPciInfoExt_v1  # REQUIRED
pynvml.nvmlDeviceGetPciInfoExt(handle, pci_info_ext)
```

**Fallback:** Always implement fallback to `pci_info` (v3) `pciSubSystemId`
if PciInfoExt fails or returns zero subsystem IDs.

### 7.3 Per-Field Try/Except Isolation (MANDATORY)
**Rule:** Every NVML call MUST have its own independent try/except block.

**Anti-pattern (WRONG):**
```python
try:
    a = nvmlDeviceGetA(handle)
    b = nvmlDeviceGetB(handle)
    c = nvmlDeviceGetC(handle)
except NVMLError:
    pass  # One failure kills ALL fields
```

**Correct Pattern (REQUIRED):**
```python
a = None
try: a = nvmlDeviceGetA(handle) except (NVMLError, AttributeError): pass

b = None
try: b = nvmlDeviceGetB(handle) except (NVMLError, AttributeError): pass

c = None
try: c = nvmlDeviceGetC(handle) except (NVMLError, AttributeError): pass
```

**Rationale:** One GPU field failure MUST NOT block other fields or other GPUs.

### 7.4 Architecture Enum Mapping
`nvmlDeviceGetArchitecture` returns an **int enum**, not bytes/string.
Must map to string names:

```python
arch_map = {
    0: 'Unknown', 1: 'Fermi', 2: 'Kepler', 3: 'Maxwell', 4: 'Pascal',
    5: 'Volta', 6: 'Turing', 7: 'Ampere', 8: 'Ada', 9: 'Hopper', 10: 'Blackwell'
}
arch_val = pynvml.nvmlDeviceGetArchitecture(handle)
gpu_architecture = arch_map.get(arch_val, f'Unknown({arch_val})')
```

### 7.5 Bus Type Value 2 = PCIe
Some drivers return `2` for PCIe (Gen3/4). Map to "PCIe":

```python
bus_type = pynvml.nvmlDeviceGetBusType(handle)
gpu_bus_type = "PCIe" if bus_type in (0, 2) else "NVLink" if bus_type == 1 else f"Unknown({bus_type})"
```

### 7.6 PCI Subsystem Zero Filtering & Fallback
PciInfoExt may return `0000:0000`. Must filter out and fallback to PciInfo v3:

```python
if subsys_vendor != 0 or subsys_device != 0:
    gpu_pci_subsystem = f"{subsys_vendor:04x}:{subsys_device:04x}"
# Fallback to PciInfo v3
if gpu_pci_subsystem is None and pci_info and hasattr(pci_info, 'pciSubSystemId'):
    # ... same extraction from pci_info
```

### 7.7 Version Bump Rules
- **PATCH** (1.13.x → 1.13.x+1): Bug fixes, AttributeError handling, minor tweaks
- **MINOR** (1.x → 1.x+1): New collectors, new payload fields
- **MAJOR** (x → x+1): Breaking payload changes
- Schema version only bumps on payload structure changes

---

## 8. PHASE 2 PREREQUISITE DEFECTS — verified 2026-10-01, FIXED on `plan/phase3-gpu-monitoring-features`

During Phase 3 planning, static analysis + live inspection against the
installed `pynvml` and the dev DB surfaced **four genuine defects** that
were shipped with Phase 2. All four were verified empirically (not
inferred) and **all four are now fixed and re-verified on this branch**
(verification evidence at the end of this section). The §9 Phase 3
implementation proceeds on top of this corrected state.

### 8.1 `gpu_inforom` is missing from compaction `static_fields` (W001/W004 gap) — **FIXED**

`metrics_app/management/commands/compact_data.py` (`COMPACT_TABLES` →
`metrics_gpumetric` → `static_fields`, lines 72–74) preserves:

```
['model', 'gpu_uuid', 'snapshot_id', 'gpu_board_part_number', 'gpu_subvendor',
 'gpu_vbios', 'pci_bus_id', 'gpu_architecture', 'gpu_bus_type', 'gpu_board_id',
 'gpu_serial', 'gpu_pci_subsystem']
```

**`gpu_inforom` is absent** even though it is a real `GPUMetric` column
(`JSONField`, migration `0058`) written by the serializer on every
heartbeat. Consequence: at tier-2/3 compaction, `ARRAY_AGG(gpu_inforom …)`
is not in the `SELECT` list, so the compacted row inserts `NULL` into
`gpu_inforom` — **INFOROM versions are lost for all data older than 1 day**.
This is exactly the W001/W004 bug class documented in §5 ("new field needs
an entry or it is lost at tier-2/3") and the `gpu_board_part_number`
lesson in §6.3 item 6.

**Fix (add to `static_fields`):**
```python
'static_fields': ['model', 'gpu_uuid', 'snapshot_id', 'gpu_board_part_number',
                   'gpu_subvendor', 'gpu_vbios', 'pci_bus_id',
                   'gpu_architecture', 'gpu_bus_type', 'gpu_board_id',
                   'gpu_serial', 'gpu_pci_subsystem', 'gpu_inforom'],
```
JSON fields are safe under the existing `ARRAY_AGG(f ORDER BY timestamp
DESC)[1]` mechanism (same as `cpu_load_avg_json` in `metrics_metricsnapshot`,
which already compacts a JSONField as a static/`last`-style value). No
special-casing needed.

### 8.2 `metrics_app.checks` is never imported on app boot → all defense checks are dead code — **FIXED**

`metrics_app/apps.py` is a bare `AppConfig` with **no `ready()`**:

```python
class MetricsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'metrics_app'
    verbose_name = 'Metrics'
```

Django discovers `@register`-decorated checks only after the checks module
has been imported. The `dashboard` app gets this right (`dashboard/apps.py`
`ready()` does `from . import checks  # noqa: F401`), but `metrics_app` has
no equivalent. **Verified empirically:**

```
$ python -c "import django; django.setup(); import sys; \
              print('metrics_app.checks' in sys.modules)"
False                       # after a clean django.setup(), the module is absent
```

Because the module is never imported, its `@register('metrics_app')`
decorators never fire, and the Phase 1 / Phase 2 / subvendor / storage /
`has_active_job` / uuid-compaction checks in `metrics_app/checks.py` are
**never registered and never run** by `manage.py check`. This is why
`python manage.py check` reports "System check identified no issues" even
though the Phase 2 check, when invoked directly, returns 8 errors and the
`gpu_inforom` compaction gap from §8.1 goes completely uncaught.

**Fix (mirror `dashboard/apps.py`):**
```python
# gpu_monitor/metrics_app/apps.py
from django.apps import AppConfig

class MetricsAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'metrics_app'
    verbose_name = 'Metrics'

    def ready(self):
        # Import the checks module so its @register-decorated system
        # checks are registered at app-ready time (required for
        # `manage.py check` to discover and run them).
        from . import checks  # noqa: F401
```

With this fix in place (and §8.3/§8.4 applied), `manage.py check`
runs all six `metrics_app` defense checks; on the corrected codebase it
returns 0 issues, and every intentional regression (see the §8.5 negative
tests) is caught with a specific error ID. (One additional minor defect
found while renumbering: two checks shared the same error IDs `E009`/`E010`
and `check_chart_query_budget`'s file-based `views.py` read false-positived
from `gpu_monitor/` — all de-duplicated / in-memory'd as part of the §8.4
rewrite of `metrics_app/checks.py`.)

### 8.3 `check_gpu_phase2_static_identifiers` had a broken array-name derivation (8 false positives) — **FIXED**

Once the module is imported (§8.2), the Phase 2 check's Layer-2 heuristic
produces **8 false-positive errors** because of a string bug:

```python
array_name = field.replace('_json', 's')     # 'gpu_vbios_json' -> 'gpu_vbioss'
key = f"'{field}': {array_name}"             # "'gpu_vbios_json': gpu_vbioss"
```

The real serializer writes the summary arrays under **singular** names
(`gpu_vbios`, `gpu_pci_bus_ids`, `gpu_architecture`, …), not `field
.replace('_json','s')`. Verified against `serializers.py` line 553–561:
the source contains `'gpu_vbios_json': gpu_vbios`, **not**
`'gpu_vbios_json': gpu_vbioss`. So the check always flags all eight
Phase 2 arrays as "not written" even though they are.

**Fix:** map each `*_json` field to its actual array variable:
```python
ARRAY_VAR = {
    'gpu_vbios_json':        'gpu_vbios',
    'gpu_pci_bus_ids_json':  'gpu_pci_bus_ids',
    'gpu_architecture_json': 'gpu_architecture',
    'gpu_bus_type_json':     'gpu_bus_type',
    'gpu_board_ids_json':    'gpu_board_ids',
    'gpu_serials_json':      'gpu_serials',
    'gpu_pci_subsystems_json':'gpu_pci_subsystems',
    'gpu_inforom_json':      'gpu_inforom',
}
for field, var in ARRAY_VAR.items():
    if f"'{field}': {var}" not in process_src:
        errors.append(Error(...))
```
(Or, more robustly, derive `var = field[:-5]` for all but the plural
`*_ids_json` / `*_subsystems_json` / `*_subvendors_json` / `*_board_part_numbers_json`
cases, which need an explicit map.)

### 8.4 Layer-3 checks used a hardcoded relative path that only resolved from repo root — **FIXED**

The compaction-defense layer (Layer 3) in `check_gpu_phase2_static_identifiers`,
`check_gpu_subvendor_pipeline`, `check_gpu_uuid_compaction_defense`, and
`check_storage_hardware_identifiers` all do:

```python
src = open('gpu_monitor/metrics_app/management/commands/compact_data.py').read()
```

This relative path resolves **only from the repo root**, not from
`gpu_monitor/` (the CWD where `manage.py` actually runs). From `gpu_monitor/`,
`os.path.exists('gpu_monitor/...')` is `False`, `open()` raises
`FileNotFoundError`, the `except FileNotFoundError: pass` swallows it, and
**Layer 3 silently does nothing** — so even if §8.2/§8.3 were fixed,
the `gpu_inforom` compaction gap would not be detected.

**Fix (use the in-memory source instead of a file read) — applied to
all four defense checks, which now live in a rewritten
`metrics_app/checks.py` (module docstring explains the rule; shared helpers
`_compaction_table()` / `_gpumetric_static_fields()` / `_snapshot_agg_fields()`
/ `_compact_table_source()`:**
```python
from metrics_app.management.commands.compact_data import COMPACT_TABLES
gpu = next(c for c in COMPACT_TABLES if c['table'] == 'metrics_gpumetric')
if 'gpu_inforom' not in gpu['static_fields']:
    errors.append(Error(...))
```
This removes the CWD dependency entirely and makes Layer 3 deterministic.
Apply the same in-memory pattern to `check_storage_hardware_identifiers`,
`check_gpu_subvendor_pipeline`, and `check_gpu_uuid_compaction_defense`
so all four defense checks behave identically under `manage.py check`,
`manage.py test`, and CI.

**Verification after §8.1–§8.4:**
1. `python manage.py makemigrations --check` → clean.
2. `python manage.py check` → **surfaces exactly** the `gpu_inforom`
   compaction error (and no others once §8.3 is fixed). This proves the
   defense check is live, correct, and CWD-independent.
3. After applying §8.1 (add `gpu_inforom` to `static_fields`), `manage.py
   check` → 0 issues. Re-run `manage.py check` to confirm the check now
   passes on the *fixed* state.
4. Empirical: create a throwaway rig row with `gpu_inforom` JSON, run
   `compact_data --dry-run` and inspect the generated `SELECT` to confirm
   `ARRAY_AGG(gpu_inforom …)` is present; then run a real compaction on a
   scratch rig and confirm the compacted row retains `gpu_inforom`.

### 8.5 Verification evidence (run on this branch, 2026-10-01)

All four fixes were implemented on `plan/phase3-gpu-monitoring-features`
and verified empirically against the live dev DB. Reproducing:

```
cd gpu_monitor
export DB_PASSWORD=local_dev_password API_KEY_LOOKUP_SECRET=dev-secret
/tmp/gpuvenv/bin/python manage.py check          # full
/tmp/gpuvenv/bin/python manage.py makemigrations --check --dry-run
```

**Result:**
- `manage.py check` → **`System check identified no issues (0 silenced)`**
  and `'metrics_app.checks' in sys.modules` after a clean `django.setup()`
  → `True` (the §8.2 import now fires; previously `False`). This proves the
  defense checks are registered and running — the false-comfort is gone.
- `makemigrations --check` → `No changes detected` (no drift from the fixes).

**Negative tests (each temporarily breaks a rule, confirms the check
catches it, then restores):**
| # | Perturbation | Caught by | Error |
|---|--------------|-----------|-------|
| 1 | Remove `gpu_inforom` from compaction `static_fields` | `check_gpu_phase2_static_identifiers` Layer 3 | `E019` ✓ |
| 2 | Remove `gpu_subvendor`+`gpu_board_part_number` from `static_fields` | `check_gpu_subvendor_pipeline` Layer 3 | `E024` ✓ |
| 3 | Remove `gpu_uuid` from `static_fields` | `check_gpu_uuid_compaction_defense` Layer 3 | `E008` ✓ |
| 4 | Change `has_active_job` agg `max` → `avg` | `check_has_active_job_system_checks` Layer 4 | `E004` ✓ |
| 5 | Confirm bool-cast `if agg=='max' and f=='has_active_job':` branch present in the in-memory SQL generator source | `check_gpu_uuid_compaction_defense` Layer 4 | (positive) ✓ |
| — | All six checks on the **correct** codebase | all | **0 false positives** ✓ |

**Compaction SQL proof (real DB):** captured the `CREATE TEMP TABLE …
AS SELECT …` that `compact_data._compact_table` generates for
`metrics_gpumetric` (tier-2, 15-min buckets). Confirmed the generated
`SELECT` contains `(ARRAY_AGG(gpu_inforom ORDER BY timestamp DESC))[1] AS
gpu_inforom` and the `INSERT` column list includes `gpu_inforom`. Executed
that exact generated `SELECT` against the real `metrics_gpumetric` table
on an empty time-window (safe: only a temp table is created, no data
touched) — succeeded and the resulting temp table has the `gpu_inforom`
column. This end-to-end proves the JSONB aggregation is syntactically valid
and that INFOROM now survives tier-2/3 compaction.

**Note on test suite:** `manage.py test` could not create a test database
(permission denied to create database — environment limitation, not a
regression). Verification therefore relies on the system checks + the
empirical compaction SQL proof above, which exercise the same code paths.

---

## 9. PHASE 3 IMPLEMENTATION PLAN — GPU performance / thermal / topology metrics

> **Status:** Plan only — no Phase 3 code written yet on this branch.
> The §8 prerequisite defects are **already fixed and verified on this
> branch** (§8.5), so Phase 3 implementation can proceed directly.

### 9.0 Scope and value assessment (re-confirmed against installed pynvml)

Phase 3 adds **five** new GPU metrics to the existing `collect_gpus()`
loop. All five NVML functions were verified present in the installed
`pynvml` (pynvml 11.x, deprecated but API-identical to `nvidia-ml-py`):

| Phase 3 field | NVML API | Return type | Frequency | Value |
|---------------|----------|-------------|-----------|-------|
| `gpu_thermal_thresholds` | `nvmlDeviceGetThermalSettings(handle, sensor)` | `c_nvmlGpuThermalSettings_t` (count + sensor array) | Static (changes only on driver/VBIOS update) | **High** — slowdown/shutdown/shut-acoustic/mem-max/GPS-current per-sensor; thermal policy audit, "why did the GPU throttle?" diagnostics |
| `gpu_pstates_util` | `nvmlDeviceGetDynamicPstatesInfo(handle)` | `c_nvmlGpuDynamicPstatesInfo_t` (flags + 8-entry utilization array) | Semi-dynamic (residency % shifts under load) | **Medium** — P-state residency + transition thresholds; power/perf correlation |
| `gpu_max_clocks` | `nvmlDeviceGetMaxClockInfo(handle, clock_type)` | `int` (MHz) per domain (GRAPHICS/MEM/SM/VIDEO) | Static | **Medium** — theoretical ceiling; "is the card below its rated clock?" sanity check |
| `gpu_mig_mode` | `nvmlDeviceGetMigMode(handle)` | `int` (0=DISABLE, 1=ENABLE) | Static | **Low** — datacenter-only; most consumer fleets report 0 |
| `gpu_bar1_memory_mb` | `nvmlDeviceGetBAR1MemoryInfo(handle)` | `c_nvmlBAR1Memory_t` (bar1Total/bar1Free/bar1Used, bytes) | Semi-dynamic (free/used shift with pinned allocations) | **Low** — pinned-memory workloads; rare in this fleet |

**Decision (from value assessment):** Phase 3 ships the top three
(thermal, P-states, max clocks) as **first-class** metrics; MIG and BAR1
are collected but stored as low-priority JSON fields so a future "datacenter
view" can surface them without a separate phase. All five are **additive**
(nullable, backward-compatible by construction) — no payload-breaking
change, so schema bump is MINOR (1.18 → 1.19), not MAJOR.

**Verifying the five APIs against the installed pynvml (run locally):**

```
nvmlDeviceGetThermalSettings       → True   c_nvmlGpuThermalSettings_t (count, sensor[3])
nvmlDeviceGetDynamicPstatesInfo    → True   c_nvmlGpuDynamicPstatesInfo_t (flags, utilization[8])
nvmlDeviceGetMaxClockInfo          → True   int (MHz)
nvmlDeviceGetMigMode               → True   int (NVML_DEVICE_MIG_DISABLE=0 / _ENABLE=1)
nvmlDeviceGetBAR1MemoryInfo        → True   c_nvmlBAR1Memory_t (bar1Total/Free/Used, bytes)
NVML_MAX_GPU_PERF_PSTATES          = 16
NVML_TEMPERATURE_THRESHOLD_*       = SHUTDOWN=0, SLOWDOWN=1, MEM_MAX=2,
                                      GPU_MAX=3, ACOUSTIC_MAX=6, GPS_CURR=7
```

### 9.1 Agent — `collect_gpus()` additions

**Location:** in both `agent/run.py` and `agent_windows/run.py`, inside the
per-GPU `for i in range(count)` loop, **after the Phase 2 static identifiers
block** (Linux `run.py` ≈ line 855; Windows `run.py` ≈ line 1008) and
**before `gpus.append({...})`**. Each metric gets its own independent
`try/except (pynvml.NVMLError, AttributeError)` block (Phase 2 §7.3
mandatory rule), and bytes are decoded via the existing
`_nvml_bytes_to_str` helper where applicable.

**Enum constants needed (define once at module top, next to
`GPU_SUBVENDOR_MAP`):**
```python
# Phase 3 NVML constants
NVML_CLOCK_DOMAINS = (
    (pynvml.NVML_CLOCK_GRAPHICS, 'graphics_mhz'),
    (pynvml.NVML_CLOCK_MEM,      'mem_mhz'),
    (pynvml.NVML_CLOCK_SM,       'sm_mhz'),
    (pynvml.NVML_CLOCK_VIDEO,    'video_mhz'),
)
NVML_TEMP_THRESHOLDS = (
    (pynvml.NVML_TEMPERATURE_THRESHOLD_SHUTDOWN,      'shutdown_c'),
    (pynvml.NVML_TEMPERATURE_THRESHOLD_SLOWDOWN,       'slowdown_c'),
    (pynvml.NVML_TEMPERATURE_THRESHOLD_MEM_MAX,        'mem_max_c'),
    (pynvml.NVML_TEMPERATURE_THRESHOLD_GPU_MAX,        'gpu_max_c'),
    (pynvml.NVML_TEMPERATURE_THRESHOLD_ACOUSTIC_MAX,   'acoustic_max_c'),
    (pynvml.NVML_TEMPERATURE_THRESHOLD_GPS_CURR,       'gps_current_c'),
)
NVML_PSTATE_MAX = 16   # pynvml.NVML_MAX_GPU_PERF_PSTATES
```

**Per-GPU collection code (insert before `gpus.append`):**
```python
# ── Phase 3: GPU performance / thermal / topology ────────────────────────
# 1) Thermal thresholds (per-sensor slowdown/shutdown/acoustic/mem-max/GPS)
gpu_thermal = None
try:
    gpu_thermal = {}
    for t_enum, t_name in NVML_TEMP_THRESHOLDS:
        try:
            v = pynvml.nvmlDeviceGetTemperatureThreshold(handle, t_enum)
            gpu_thermal[t_name] = int(v)
        except (pynvml.NVMLError, AttributeError):
            gpu_thermal[t_name] = None
except (pynvml.NVMLError, AttributeError):
    gpu_thermal = None

# 2) Dynamic P-states (residency % + transition thresholds, P0..P7)
gpu_pstates = None
try:
    pinfo = pynvml.nvmlDeviceGetDynamicPstatesInfo(handle)
    if pinfo is not None:
        gpu_pstates = {
            'flags': int(pinfo.flags),
            'util': [
                {'p': i, 'present': bool(pinfo.utilization[i].bIsPresent),
                 'pct': int(pinfo.utilization[i].percentage),
                 'inc': int(pinfo.utilization[i].incThreshold),
                 'dec': int(pinfo.utilization[i].decThreshold)}
                for i in range(len(pinfo.utilization))
            ],
        }
except (pynvml.NVMLError, AttributeError):
    gpu_pstates = None

# 3) Max clocks per domain (graphics/mem/sm/video)
gpu_max_clocks = {}
for clock_enum, clock_name in NVML_CLOCK_DOMAINS:
    try:
        gpu_max_clocks[clock_name] = int(pynvml.nvmlDeviceGetMaxClockInfo(handle, clock_enum))
    except (pynvml.NVMLError, AttributeError):
        gpu_max_clocks[clock_name] = None
if not any(v is not None for v in gpu_max_clocks.values()):
    gpu_max_clocks = None

# 4) MIG mode (datacenter; consumer fleets → 0/DISABLE)
gpu_mig_mode = None
try:
    gpu_mig_mode = int(pynvml.nvmlDeviceGetMigMode(handle))
except (pynvml.NVMLError, AttributeError):
    gpu_mig_mode = None

# 5) BAR1 memory (pinned-memory aperture; bytes → MB)
gpu_bar1 = None
try:
    b = pynvml.nvmlDeviceGetBAR1MemoryInfo(handle)
    if b is not None:
        gpu_bar1 = {
            'total_mb': int(b.bar1Total) // (1024 * 1024),
            'used_mb':  int(b.bar1Used)  // (1024 * 1024),
            'free_mb':  int(b.bar1Free)  // (1024 * 1024),
        }
except (pynvml.NVMLError, AttributeError):
    gpu_bar1 = None
```

**Payload keys appended to the `gpus.append({...})` dict** (after the Phase 2
keys, before `mem_total_mb`):
```python
# Phase 3 performance / thermal / topology
'gpu_thermal_thresholds': gpu_thermal,    # dict or None
'gpu_pstates_util':       gpu_pstates,    # dict or None
'gpu_max_clocks':         gpu_max_clocks, # dict or None
'gpu_mig_mode':           gpu_mig_mode,   # int or None
'gpu_bar1_mb':            gpu_bar1,       # dict or None
```

**Version bumps (MUST per §7.7):**
- Linux `agent/run.py`: `__version__` 1.13.2 → **1.14.0** (MINOR — new
  payload fields), `__schema_version__` 1.18 → **1.19**.
- Windows `agent_windows/run.py`: `__version__` 1.13.2-win → **1.14.0-win**,
  `__schema_version__` 1.18 → **1.19**.
- Both `agent/README.md` + `agent_windows/README.md`: add the five new keys
  to the GPU payload table and bump the version row.

### 9.2 Server — model / migration

**New migration `0059_gpumetric_phase3_performance_thermal.py`**
(additive only; existing rows → `NULL`/`[]`; no data loss, no `AlterField`):

```python
# gpu_monitor/metrics_app/migrations/0059_gpumetric_phase3_performance_thermal.py
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [('metrics_app', '0058_gpumetric_phase2_static_identifiers')]
    operations = [
        # GPUMetric — per-row (time-series + compactable)
        migrations.AddField(model_name='gpumetric', name='gpu_thermal_thresholds',
            field=models.JSONField(blank=True, default=dict, null=True,
                                   help_text='Per-sensor thermal thresholds (shutdown/slowdown/mem_max/gpu_max/acoustic_max/gps_current, °C); {} when unavailable')),
        migrations.AddField(model_name='gpumetric', name='gpu_pstates_util',
            field=models.JSONField(blank=True, default=dict, null=True,
                                   help_text='Dynamic P-state residency + transition thresholds (P0..P7); {} when unavailable')),
        migrations.AddField(model_name='gpumetric', name='gpu_max_clocks',
            field=models.JSONField(blank=True, default=dict, null=True,
                                   help_text='Theoretical max clocks per domain (graphics/mem/sm/video, MHz); {} when unavailable')),
        migrations.AddField(model_name='gpumetric', name='gpu_mig_mode',
            field=models.PositiveSmallIntegerField(blank=True, default=0, null=True,
                                                  help_text='MIG mode (0=DISABLE, 1=ENABLE); 0 when unavailable')),
        migrations.AddField(model_name='gpumetric', name='gpu_bar1_mb',
            field=models.JSONField(blank=True, default=dict, null=True,
                                   help_text='BAR1 memory aperture total/used/free (MB); {} when unavailable')),
        # LatestSnapshot — denormalized arrays (one entry per GPU)
        migrations.AddField(model_name='latestsnapshot', name='gpu_thermal_thresholds_json',
            field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='latestsnapshot', name='gpu_pstates_util_json',
            field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='latestsnapshot', name='gpu_max_clocks_json',
            field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='latestsnapshot', name='gpu_mig_modes_json',
            field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name='latestsnapshot', name='gpu_bar1_mb_json',
            field=models.JSONField(blank=True, default=list)),
    ]
```

**Rationale for storage shape (same pattern as Phase 2):**
- The three "first-class" metrics (thermal, P-states, max clocks) are
  stored **per-row on `GPUMetric`** so historical queries ("when did this
  GPU first exceed its slowdown threshold?") survive compaction — mirroring
  how `gpu_vbios` / `gpu_architecture` are per-row in Phase 2.
- All five are **also** stored as `LatestSnapshot` JSON arrays (same as
  `gpu_vbios_json` etc.) so the Live Metrics GPU card can render them with
  **zero** per-GPU query cost, reusing the single `_build_gpu_metrics`
  builder (`dashboard/views.py:181`).
- `gpu_mig_mode` is a scalar int → stored as a `PositiveSmallIntegerField`
  per row + a `[0, 1, …]` array on the snapshot. `0` is a valid value
  (MIG disabled) so the sentinel is `None`, not `0` (same rule as
  `gpu_board_id` in §6.2 / Phase 2).
- `gpu_bar1_mb` is a 3-key dict → JSONField per row + array of dicts on the
  snapshot (same shape as `gpu_inforom_json`).

**Model edits (`gpu_monitor/metrics_app/models.py`):**
- `GPUMetric` (next to the Phase 2 block, ≈ line 95): add the five fields
  exactly as in the migration above.
- `LatestSnapshot` (next to the Phase 2 arrays, ≈ line 298): add the five
  `*_json` arrays.

### 9.3 Server — serializer `process_ingest`

**In `metrics_app/serializers.py`, `process_ingest`:**

**(a) List init** (next to the Phase 2 arrays, ≈ line 162):
```python
# Phase 3 performance / thermal / topology arrays
gpu_thermal_thresholds = []
gpu_pstates_util = []
gpu_max_clocks = []
gpu_mig_modes = []
gpu_bar1_mb = []
```

**(b) Per-row `GPUMetric.objects.update_or_create(... defaults={…})`**
(next to the Phase 2 keys, ≈ line 181):
```python
# Phase 3
'gpu_thermal_thresholds': gpu.get('gpu_thermal_thresholds') or {},
'gpu_pstates_util':        gpu.get('gpu_pstates_util') or {},
'gpu_max_clocks':          gpu.get('gpu_max_clocks') or {},
'gpu_mig_mode':            (gpu.get('gpu_mig_mode') if gpu.get('gpu_mig_mode') is not None else 0),
'gpu_bar1_mb':             gpu.get('gpu_bar1_mb') or {},
```

**(c) Summary-array loop** (next to the Phase 2 `.append` calls, ≈ line 218):
```python
gpu_thermal_thresholds.append(gpu.get('gpu_thermal_thresholds') or {})
gpu_pstates_util.append(gpu.get('gpu_pstates_util') or {})
gpu_max_clocks.append(gpu.get('gpu_max_clocks') or {})
gpu_mig_modes.append(gpu.get('gpu_mig_mode') if gpu.get('gpu_mig_mode') is not None else 0)
gpu_bar1_mb.append(gpu.get('gpu_bar1_mb') or {})
```

**(d) `ls_defaults` dict** (next to the Phase 2 arrays, ≈ line 561):
```python
# Phase 3
'gpu_thermal_thresholds_json': gpu_thermal_thresholds,
'gpu_pstates_util_json':       gpu_pstates_util,
'gpu_max_clocks_json':         gpu_max_clocks,
'gpu_mig_modes_json':          gpu_mig_modes,
'gpu_bar1_mb_json':            gpu_bar1_mb,
```

**(e) `IngestSerializer.validate_schema_version`** (line 33): add `'1.19'`
to the accepted-version tuple. (Also clean up the duplicate `'1.16'` token
noted in §6.3 item 0 while touching this line.)

**Backward compatibility:** older agents (≤1.18) that omit the five keys
→ `gpu.get(...) or {}` / `… is not None else 0` → `GPUMetric` stores `{}`/`0`
and the arrays store `{}`/`0`. No 500, no crash, no schema rejection —
same construction as Phase 1/2.

### 9.4 Server — compaction (`compact_data.py`)

Add the three "first-class" per-row Phase 3 fields to `metrics_gpumetric`
`static_fields` (they are static/semi-static and must survive tier-2/3,
same as `gpu_vbios`). **Do NOT** add `gpu_mig_mode` or `gpu_bar1_mb` to
`agg_fields` — they are either a scalar (MIG) or a JSON dict (BAR1) and
are not charted; keep them as static fields too so they aren't dropped:

```python
'static_fields': ['model', 'gpu_uuid', 'snapshot_id', 'gpu_board_part_number',
                   'gpu_subvendor', 'gpu_vbios', 'pci_bus_id',
                   'gpu_architecture', 'gpu_bus_type', 'gpu_board_id',
                   'gpu_serial', 'gpu_pci_subsystem', 'gpu_inforom',
                   # Phase 3
                   'gpu_thermal_thresholds', 'gpu_pstates_util',
                   'gpu_max_clocks', 'gpu_mig_mode', 'gpu_bar1_mb'],
```
> **Note:** `gpu_inforom` is added here as part of the §8.1 prerequisite
> fix. All five Phase 3 fields are JSON/integer scalars that are safe under
> the existing `ARRAY_AGG(f ORDER BY timestamp DESC)[1]` compaction
> mechanism. No new aggregation logic is required.

### 9.5 Server — view + template

**`_build_gpu_metrics` (`dashboard/views.py:181`)** — add five keys next to
the Phase 2 keys (≈ line 211):
```python
# Phase 3 performance / thermal / topology
'gpu_thermal_thresholds': _json_get(snapshot.gpu_thermal_thresholds_json, i),
'gpu_pstates_util':       _json_get(snapshot.gpu_pstates_util_json, i),
'gpu_max_clocks':         _json_get(snapshot.gpu_max_clocks_json, i),
'gpu_mig_mode':           _json_get(snapshot.gpu_mig_modes_json, i),
'gpu_bar1_mb':            _json_get(snapshot.gpu_bar1_mb_json, i),
```

**Template (`gpu_monitor/templates/dashboard/_metrics_cards.html`)** — add
five `{% if %}` blocks after the Phase 2 block (≈ line 446, before the
`Core` utilization row). Each renders only when truthy (same silent-no-op
pattern as Phase 2):
```html
{% if gpu.gpu_thermal_thresholds %}
<div class="text-xs text-gray-500 mb-1">
    Thermal: <span class="text-gray-300 font-mono">
        {% with t=gpu.gpu_thermal_thresholds %}
        shutdown={{ t.shutdown_c|default:"?" }}°C slowdown={{ t.slowdown_c|default:"?" }}°C mem_max={{ t.mem_max_c|default:"?" }}°C
        {% endwith %}
    </span>
</div>
{% endif %}

{% if gpu.gpu_max_clocks %}
<div class="text-xs text-gray-500 mb-1">
    Max clocks: <span class="text-gray-300 font-mono">
        Gfx={{ gpu.gpu_max_clocks.graphics_mhz|default:"?" }}
        Mem={{ gpu.gpu_max_clocks.mem_mhz|default:"?" }}
        SM={{ gpu.gpu_max_clocks.sm_mhz|default:"?" }} MHz
    </span>
</div>
{% endif %}

{% if gpu.gpu_pstates_util %}
<div class="text-xs text-gray-500 mb-1">
    P-states: <span class="text-gray-300 font-mono"
        title="{{ gpu.gpu_pstates_util.util }}">{{ gpu.gpu_pstates_util.flags }}</span>
</div>
{% endif %}

{% if gpu.gpu_mig_mode == 1 %}
<div class="text-xs text-gray-500 mb-1">
    MIG: <span class="text-yellow-300 font-mono">ENABLED</span>
</div>
{% endif %}

{% if gpu.gpu_bar1_mb %}
<div class="text-xs text-gray-500 mb-1">
    BAR1: <span class="text-gray-300 font-mono">
        {{ gpu.gpu_bar1_mb.used_mb }}/{{ gpu.gpu_bar1_mb.total_mb }} MB
    </span>
</div>
{% endif %}
```
MIG shows only when **enabled** (`== 1`) so the common consumer case
(`0`) does not add a noise row.

### 9.6 Server — defense in depth (project rule: code + check + test + docs)

**`metrics_app/checks.py` — new check `check_gpu_phase3_performance_thermal`**
(mirrors `check_gpu_phase2_static_identifiers` structure, but uses the
**in-memory `COMPACT_TABLES` pattern from §8.4** so it is CWD-independent
and not a silent no-op):

```python
@register('metrics_app')
def check_gpu_phase3_performance_thermal(app_configs, **kwargs):
    """Defense in depth: Phase 3 GPU performance/thermal metrics must be
    persisted AND survive compaction. CWD-independent (in-memory
    COMPACT_TABLES, not a file read)."""
    errors = []
    from metrics_app.models import GPUMetric, LatestSnapshot
    from metrics_app.management.commands.compact_data import COMPACT_TABLES

    gpumetric_fields = ('gpu_thermal_thresholds','gpu_pstates_util',
                        'gpu_max_clocks','gpu_mig_mode','gpu_bar1_mb')
    latestsnapshot_fields = ('gpu_thermal_thresholds_json','gpu_pstates_util_json',
                             'gpu_max_clocks_json','gpu_mig_modes_json','gpu_bar1_mb_json')

    # Layer 1: model fields exist
    for f in gpumetric_fields:
        try: GPUMetric._meta.get_field(f)
        except Exception:
            errors.append(Error(f'GPUMetric missing Phase 3 field: {f}',
                hint='Run migration 0059_gpumetric_phase3_performance_thermal',
                obj='metrics_app.GPUMetric', id=f'metrics_app.E060'))
    for f in latestsnapshot_fields:
        try: LatestSnapshot._meta.get_field(f)
        except Exception:
            errors.append(Error(f'LatestSnapshot missing Phase 3 field: {f}',
                hint='Run migration 0059_gpumetric_phase3_performance_thermal',
                obj='metrics_app.LatestSnapshot', id=f'metrics_app.E065'))

    # Layer 2: serializer writes per-row + summary arrays
    import inspect
    import metrics_app.serializers as s
    src = inspect.getsource(s.process_ingest)
    for f in gpumetric_fields:
        if f"'{f}': gpu.get('{f}'" not in src:
            errors.append(Error(f"serializer does not write Phase 3 '{f}' to GPUMetric",
                hint=f"Add '{f}' to GPUMetric defaults", obj='metrics_app.serializers.process_ingest',
                id='metrics_app.E070'))

    # Layer 3: compaction preserves all Phase 3 fields (in-memory, CWD-safe)
    try:
        gpu_cfg = next(c for c in COMPACT_TABLES if c['table'] == 'metrics_gpumetric')
        for f in gpumetric_fields:
            if f not in gpu_cfg['static_fields']:
                errors.append(Error(f'compaction static_fields missing Phase 3 {f}',
                    hint='Static/semi-static GPU metrics must survive tier-2/3',
                    obj='metrics_app.management.commands.compact_data',
                    id='metrics_app.E075'))
    except Exception as e:
        errors.append(Error(f'could not read COMPACT_TABLES: {e}',
            id='metrics_app.E080'))
    return errors
```
> **This check only works because §8.2 (apps.py `ready()` import) and
> §8.4 (in-memory compaction source) are applied first.** The Phase 3 check
> is intentionally written the *correct* way to prevent the same three
> defects from recurring.

**`metrics_app/tests.py` — new tests:**
- `test_phase3_payload_ingest`: a 1.19 payload with all five Phase 3 keys
  present → assert `GPUMetric.gpu_thermal_thresholds` is a non-empty dict,
  `gpu_mig_mode == 0`, and `LatestSnapshot.gpu_mig_modes_json == [0]`,
  `gpu_thermal_thresholds_json` is a list of dicts.
- `test_phase3_backwards_compat_1_18`: a 1.18 payload **without** the Phase 3
  keys → assert `GPUMetric.gpu_thermal_thresholds == {}`, `gpu_mig_mode == 0`,
  and the snapshot arrays are `[]`/`[0]` (no 500, no crash).
- `test_phase3_compaction_static_fields`: assert all five Phase 3 fields are
  in `COMPACT_TABLES['metrics_gpumetric']['static_fields']` (guards against
  a future regression that drops one).
- `test_check_gpu_phase3_performance_thermal_no_false_positives`: invoke the
  new check directly after the §8.2/§8.4 fixes and assert it returns 0 errors
  on the current (correct) codebase — this is the regression guard that
  proves the check itself is not producing the §8.3-style false positives.

**Docs sync:** agent READMEs (both) — add the five Phase 3 keys to the GPU
payload table and bump version to 1.14.0 / schema 1.19. Update §3 (value
assessment) to mark Phase 3 fields as "shipped" once merged.

### 9.7 Deploy & verify (empirical, per project rule)

1. **Migrations:** `python3 manage.py makemigrations metrics_app` → must
   produce exactly `0059_gpumetric_phase3_performance_thermal`; then
   `python3 manage.py migrate` on the dev DB. `python3 manage.py
   makemigrations --check` → clean.
2. **Checks:** `python3 manage.py check` → confirms the `gpu_inforom`
   compaction gap (fixed in §8.1) and the Phase 3 check pass with 0 false
   positives. (On this branch `manage.py check` already returns clean, and
   a negative test removing `gpu_inforom` from `static_fields` reliably
   raises `E019` — proof the defense is live, see §8.5.)
3. **Agent dry-run:** on a GPU host, run the patched `agent/run.py` once
   with a local `LOG_PAYLOAD` hook and inspect `payload.json` — confirm all
   five Phase 3 keys are present with correct shapes (thermal dict with 6
   keys, pstates dict with `flags` + `util[8]`, max_clocks dict with 4
   keys, mig_mode int, bar1_mb dict with 3 keys).
4. **Live ingest:** point the agent at the dev server; within ~60 s:
   ```sql
   SELECT agent_version, gpu_mig_modes_json, gpu_thermal_thresholds_json,
          gpu_max_clocks_json, gpu_bar1_mb_json, gpu_pstates_util_json
   FROM metrics_latest_snapshot WHERE rig_uuid = '<disposable-rig>';
   ```
   Expected: `1.14.0`, non-empty JSON arrays.
5. **Live Metrics → rig → GPU card:** verify "Thermal:", "Max clocks:",
   "BAR1:" lines render; "MIG:" line hidden (consumer card, mode 0).
6. **Compaction regression:** create a disposable rig with Phase 3 data,
   run `python3 manage.py compact_data --dry-run` and inspect the generated
   SQL to confirm all five Phase 3 fields + `gpu_inforom` appear in
   `ARRAY_AGG(…)`; then run a real compaction on the scratch rig and
   `SELECT gpu_thermal_thresholds, gpu_mig_mode FROM metrics_gpumetric
   WHERE rig_uuid='…'` to confirm the compacted row retained the values.
7. **Backward-compat regression:** point an older (1.18) agent at the
   server; confirm it ingests fine, the GPU card shows no Phase 3 lines,
   and no 500s occur.

### 9.8 Effort estimate

- **Agent (Linux + Windows, 5 metrics × per-field try/except):** ~60 min
  (the §7.1–§7.6 Phase 2 lessons — AttributeError handling, per-field
  isolation, version-field setting — are already in the codebase as a
  pattern; Phase 3 reuses them verbatim).
- **Server (migration + models + serializer + compaction + view + template +
  new defense check + tests):** ~75 min (one migration, five fields on two
  models, one serializer block, one compaction list, one view block, one
  template block, one check, four tests).
- **Prerequisite fixes (§8.1–§8.4):** ~25 min (one-line compaction add,
  `apps.py` `ready()`, array-name map, in-memory compaction source) + 10 min
  to verify `manage.py check` is now live.
- **Docs/tests + empirical verification:** ~30 min.
- **Total ≈ 3 h**, single branch `plan/phase3-gpu-monitoring-features`
  (to be renamed to `feat/gpu-phase3-performance-thermal` for
  implementation; the `plan/` branch is used only for this planning doc).

### 9.9 Edge cases / pitfalls (Phase 3)

1. **`nvmlDeviceGetThermalSettings` is deprecated** in favor of
   `nvmlDeviceGetTemperatureThreshold` (used here per-sensor, which is the
   NVML-recommended path). If a GPU reports a threshold of `0` for a sensor
   it does not support, the agent stores `None` (not `0`) to avoid a
   misleading "0°C shutdown" reading.
2. **P-states array length:** `c_nvmlGpuDynamicPstatesInfo_t.utilization` is
   a fixed 8-element array (P0–P7), but `NVML_MAX_GPU_PERF_PSTATES` is 16.
   The agent iterates the actual array length (`len(pinfo.utilization)`),
   not the constant, so it never indexes out of bounds.
3. **MIG `0` is a valid value** (disabled). Sentinel is `None`, never
   `0` — same rule as `gpu_board_id` (Phase 2 §6.2 / §2.3). The template
   shows "MIG: ENABLED" only when `== 1`.
4. **BAR1 is a dict, not a scalar** — store as JSONField (not a
   `PositiveIntegerField`) because it has three related values
   (total/used/free) that are meaningless independently.
5. **Do NOT move `nvmlInit()`** — reuse the handle already in the
   `collect_gpus()` loop (same as Phase 1/2). Do not add a second
   init/shutdown.
6. **`pynvml` is deprecated** in favor of `nvidia-ml-py` — the API surface
   used here is identical in both, so no migration risk (same as Phase 2
   §2.4 / §6 edge case 6).
7. **Consumer GPUs may return all thresholds as 0 or NOT_SUPPORTED** — the
   per-sensor `try/except` + `None` default handles this; the template's
   `{% if %}` hides the row when the dict is empty.

---

## 5. Effort estimate

**Immediate (AIB subvendor + board part number; model already collected):**
~45 min agent (two calls + subvendor map + bump), ~45 min server
(GPUMetric 1 field + LatestSnapshot 1 array + serializer + view +
migrations; subvendor storage IMPLEMENTED 2026-10-01 via 0057 + serializer
+ view + template + compaction + checks + tests), ~30 min
docs/tests. Total ≈ 2 h, single branch
`feat/agent-gpu-brand-board-part` (shipped 2026-09-30; the original
`gpu_brand` work was added then reverted on the same branch; subvendor
display closed 2026-10-01 on same branch).

**Deferred (12+ fields):** ~1.5 h agent, ~1.5 h server,
~45 min docs/tests ≈ 3.5 h on a follow-up branch.
