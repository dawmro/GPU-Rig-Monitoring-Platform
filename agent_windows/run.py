#!/usr/bin/env python3
"""
GPU Rig Monitoring Agent (Windows)

Collects hardware/software metrics and sends them to the monitoring server.
Designed to run via Windows Task Scheduler.

Usage:
    python run.py

Config file: config.yaml (in the same directory as this script)

Dependencies:
    pip install psutil py-cpuinfo requests pyyaml wmi

Optional dependencies:
    pip install pynvml         # For NVIDIA GPU monitoring (requires NVIDIA GPU)

Notes:
    - Docker container monitoring uses the `docker` CLI via subprocess.
      No Python SDK required. Docker Desktop must be running on Windows.
    - The `docker` CLI must be in the system PATH.

Versioning:
    - __version__ (MAJOR.MINOR.PATCH): incremented for agent-side changes
      (collectors, payload format, bug fixes).
    - __schema_version__ (MAJOR.MINOR): incremented when payload structure
      changes in a way that affects server serialization/storage.

    After making changes to agent code, you MUST increment __version__ and/or
    __schema_version__ according to the depth of changes:
    - PATCH: bug fixes, minor collector tweaks (e.g. 1.5.0 → 1.5.1)
    - MINOR: new collectors, new payload fields (e.g. 1.5.0 → 1.6.0)
    - MAJOR: breaking changes to payload structure (e.g. 1.4 → 2.0)

    See docs/GPU_Rig_Monitoring_Architecture.md §3.1a for full versioning rules.
"""

import os
import sys
import json
import re
import time
import random
import logging
import logging.handlers
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
import requests

__version__ = '1.6.11-win'
__schema_version__ = '1.8'

# ── Config ──────────────────────────────────────────────────────────────────

def get_default_config_path():
    """Return platform-appropriate default config path."""
    script_dir = Path(__file__).resolve().parent
    return str(script_dir / 'config.yaml')


def load_config(path=None):
    """Load and validate configuration."""
    if path is None:
        path = get_default_config_path()

    with open(path, 'r') as f:
        config = yaml.safe_load(f) or {}

    required = ['api_key', 'server_endpoint']
    for field in required:
        if not config.get(field):
            print(f"ERROR: Missing required config field: {field}", file=sys.stderr)
            sys.exit(2)

    # Validate server_endpoint has a scheme
    endpoint = config.get('server_endpoint', '')
    if not endpoint.startswith(('http://', 'https://')):
        print(f"ERROR: server_endpoint must start with http:// or https://. Got: {endpoint}", file=sys.stderr)
        sys.exit(2)

    # Auto-generate UUID on first run
    if config.get('rig_uuid') == 'auto' or not config.get('rig_uuid'):
        config['rig_uuid'] = str(uuid.uuid4())
        try:
            config_path = Path(path)
            existing = yaml.safe_load(config_path.read_text()) or {}
            existing['rig_uuid'] = config['rig_uuid']
            config_path.write_text(yaml.dump(existing))
        except Exception:
            pass

    # Set default rig_name from config or hostname
    if not config.get('rig_name'):
        config['rig_name'] = platform.node() or 'Unnamed Rig'

    return config


# ── Logging ─────────────────────────────────────────────────────────────────

def setup_logging(debug=False):
    log_dir = Path(__file__).resolve().parent / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO
    fmt = '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'

    handler = logging.handlers.RotatingFileHandler(
        log_dir / 'agent.log', maxBytes=10*1024*1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter(fmt))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    root.addHandler(console)


def log_payload(payload):
    """Save the latest full JSON payload to payload.json for local analysis."""
    log_dir = Path(__file__).resolve().parent / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    payload_path = log_dir / 'payload.json'
    payload_path.write_text(json.dumps(payload, indent=2, default=str) + '\n')


# ── Metric Collectors (all-in-one, no duplication) ─────────────────────────

def collect_cpu():
    """Collect all CPU metrics: static info + time-series data."""
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count_phys = psutil.cpu_count(logical=False)
        cpu_count_log = psutil.cpu_count(logical=True)

        # Windows doesn't have os.getloadavg(); use CPU percent as proxy
        load_avg = [cpu_percent / 100.0 * cpu_count_log] * 3

        temp_c = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        temp_c = entries[0].current
                        break
        except Exception:
            # sensors_temperatures() may not be available on Windows
            pass

        model = 'Unknown'
        try:
            import cpuinfo
            info = cpuinfo.get_cpu_info()
            model = info.get('brand_raw', 'Unknown')
        except Exception:
            pass

        # Fallback: use WMI on Windows if cpuinfo didn't work
        if model == 'Unknown' and platform.system() == 'Windows':
            try:
                import wmi
                c = wmi.WMI()
                for proc in c.Win32_Processor():
                    model = proc.Name.strip()
                    break
            except Exception:
                pass

        return {
            'model': model,
            'physical_cores': cpu_count_phys,
            'logical_cores': cpu_count_log,
            'load_avg': load_avg,
            'utilization_pct': cpu_percent,
            'temp_c': temp_c,
        }
    except Exception as e:
        logging.getLogger('cpu').warning('CPU collection failed: %s', e)
        return {}


def collect_memory():
    """Collect all memory metrics: total, used, free, cached, swap."""
    try:
        import psutil
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            'total_bytes': vm.total,
            'used_bytes': vm.used,
            'free_bytes': vm.available,
            'cached_bytes': getattr(vm, 'cached', None),
            'swap_used_bytes': swap.used,
            'swap_total_bytes': swap.total,
        }
    except Exception as e:
        logging.getLogger('memory').warning('Memory collection failed: %s', e)
        return {}


def collect_motherboard():
    """Collect motherboard/system info via WMI on Windows."""
    result = {}
    if platform.system() == 'Windows':
        try:
            import wmi
            c = wmi.WMI()
            for board in c.Win32_BaseBoard():
                result['manufacturer'] = board.Manufacturer.strip()
                result['model'] = board.Product.strip()
                break
            for bios in c.Win32_BIOS():
                result['bios_version'] = bios.SMBIOSBIOSVersion.strip()
                break
        except Exception:
            pass
    else:
        try:
            for field, path in [
                ('manufacturer', '/sys/class/dmi/id/board_vendor'),
                ('model', '/sys/class/dmi/id/board_name'),
                ('bios_version', '/sys/class/dmi/id/bios_version'),
            ]:
                try:
                    result[field] = Path(path).read_text().strip()
                except Exception:
                    result[field] = 'unknown'
        except Exception:
            pass
    return result


def _get_windows_disk_io():
    """Get per-physical-disk I/O counters on Windows.

    psutil returns keys like 'PhysicalDrive0', 'PhysicalDrive1'.
    Returns dict: { 'PhysicalDrive0': {read_bytes, write_bytes, read_iops, write_iops, busy_time_ms}, ... }
    Note: busy_time_ms is only available on Linux; on Windows it will be None.
    """
    try:
        import psutil
        io = psutil.disk_io_counters(perdisk=True)
        if not io:
            return {}
    except Exception:
        return {}

    result = {}
    for name, counters in io.items():
        if not name.startswith('PhysicalDrive'):
            continue
        result[name] = {
            'read_bytes': counters.read_bytes,
            'write_bytes': counters.write_bytes,
            'read_iops': counters.read_count,
            'write_iops': counters.write_count,
            'busy_time_ms': getattr(counters, 'busy_time', None),
        }
    return result


def _get_drive_to_physical_map():
    """Build a mapping from Windows drive letter to PhysicalDrive name.

    Uses wmic CLI commands to get the association chain:
    1. Win32_LogicalDiskToPartition: logical disk → partition
    2. Win32_DiskDriveToDiskPartition: disk drive → partition

    Combines both to build: drive letter → PhysicalDriveN

    Returns dict: {'C': 'PhysicalDrive0', 'D': 'PhysicalDrive1', ...}
    """
    drive_map = {}

    try:
        # Step 1: Get logical disk → partition mapping
        # Output format: Node,Antecedent,Dependent
        # Antecedent: Win32_DiskPartition.DeviceID="Disk #0, Partition #0"
        # Dependent: Win32_LogicalDisk.DeviceID="C:"
        logical_to_partition = {}
        out = subprocess.run(
            ['wmic', 'path', 'Win32_LogicalDiskToPartition', 'get',
             'Antecedent,Dependent', '/format:csv'],
            capture_output=True, text=True, timeout=15
        )
        if out.returncode != 0:
            logging.getLogger('storage').debug('wmic LogicalDiskToPartition failed')
            return _get_drive_to_physical_map_wmi()

        import re
        for line in out.stdout.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('Node') or line.startswith('Antecedent'):
                continue
            # Extract partition info from Antecedent: Disk #N, Partition #M
            part_match = re.search(r'Disk #(\d+), Partition #(\d+)', line)
            # Extract drive letter from Dependent: DeviceID="X:"
            drive_match = re.search(r'DeviceID="([A-Z]):"', line)
            if part_match and drive_match:
                disk_idx = int(part_match.group(1))
                part_idx = int(part_match.group(2))
                letter = drive_match.group(1)
                logical_to_partition[(disk_idx, part_idx)] = letter

        if not logical_to_partition:
            logging.getLogger('storage').debug('wmic LogicalDiskToPartition returned no data')
            return _get_drive_to_physical_map_wmi()

        # Step 2: Get disk drive → partition mapping
        # Antecedent: Win32_DiskDrive.DeviceID="\\.\PHYSICALDRIVE0"
        # Dependent: Win32_DiskPartition.DeviceID="Disk #0, Partition #0"
        disk_to_partition = {}
        out = subprocess.run(
            ['wmic', 'path', 'Win32_DiskDriveToDiskPartition', 'get',
             'Antecedent,Dependent', '/format:csv'],
            capture_output=True, text=True, timeout=15
        )
        if out.returncode != 0:
            logging.getLogger('storage').debug('wmic DiskDriveToDiskPartition failed')
            return _get_drive_to_physical_map_wmi()

        for line in out.stdout.strip().split('\n'):
            line = line.strip()
            if not line or line.startswith('Node') or line.startswith('Antecedent'):
                continue
            # Extract PhysicalDrive index from Antecedent
            phys_match = re.search(r'PHYSICALDRIVE(\d+)', line)
            # Extract partition info from Dependent: Disk #N, Partition #M
            part_match = re.search(r'Disk #(\d+), Partition #(\d+)', line)
            if phys_match and part_match:
                disk_idx = int(phys_match.group(1))
                part_idx = int(part_match.group(2))
                disk_to_partition[(disk_idx, part_idx)] = disk_idx

        # Step 3: Combine both mappings
        # logical_to_partition: (disk_idx, part_idx) → letter
        # disk_to_partition: (disk_idx, part_idx) → disk_idx
        # Result: letter → PhysicalDriveN
        for (disk_idx, part_idx), letter in logical_to_partition.items():
            if (disk_idx, part_idx) in disk_to_partition:
                drive_map[letter] = f'PhysicalDrive{disk_idx}'

        if drive_map:
            logging.getLogger('storage').debug('Drive map from wmic: %s', drive_map)
            return drive_map

    except Exception as e:
        logging.getLogger('storage').debug('wmic drive mapping failed: %s', e)

    return _get_drive_to_physical_map_wmi()


def _get_drive_to_physical_map_wmi():
    """Fallback: build drive-to-physical map using Python WMI module.

    Iterates through all disk drives, their partitions, and logical disks
    to build the complete mapping.
    """
    drive_map = {}
    try:
        import wmi
        c = wmi.WMI()
        for dd in c.Win32_DiskDrive():
            disk_idx = getattr(dd, 'Index', None)
            if disk_idx is None:
                continue
            for partition in dd.associators("Win32_DiskDriveToDiskPartition"):
                for logical_disk in partition.associators("Win32_LogicalDiskToPartition"):
                    drive_letter = logical_disk.DeviceID[0]  # e.g. 'C'
                    drive_map[drive_letter] = f'PhysicalDrive{disk_idx}'
        logging.getLogger('storage').debug('Drive map from WMI: %s', drive_map)
    except Exception as e:
        logging.getLogger('storage').debug('WMI drive mapping failed: %s', e)
    return drive_map


def _partition_letter_to_physical(device_str):
    """Map a Windows drive letter to its PhysicalDrive name.

    This is a compatibility wrapper that uses the pre-built drive map.
    The actual mapping is done once in collect_storage() via _get_drive_to_physical_map().

    Input: 'C:\\' or 'D:\\'
    Output: 'PhysicalDrive0' or similar, or None if not found.
    """
    # This function is kept for compatibility but the actual mapping
    # is now done via _get_drive_to_physical_map() in collect_storage()
    drive_letter = device_str[0].upper()
    try:
        import wmi
        c = wmi.WMI()
        for ld in c.Win32_LogicalDisk(DriveType=3):
            if ld.DeviceID.startswith(drive_letter):
                for assoc in ld.associators("Win32_LogicalDiskToPartition"):
                    for pdisk in assoc.associators("Win32_DiskPartitionToDiskDrive"):
                        idx = getattr(pdisk, 'Index', None)
                        if idx is not None:
                            return f'PhysicalDrive{idx}'
    except Exception:
        pass
    return None


def _windows_partition_to_physical(device_str):
    """Map a Windows partition device string to its PhysicalDrive name.

    Input: 'C:\\' or 'D:\\' (from psutil.disk_partitions().device)
    Output: 'PhysicalDrive0' or similar, or None if not found.
    """
    try:
        import wmi
        c = wmi.WMI()
        drive_letter = device_str[0].upper()
        for ld in c.Win32_LogicalDisk(DriveType=3):
            if ld.DeviceID.startswith(drive_letter):
                for assoc in ld.associators("Win32_LogicalDiskToPartition"):
                    for pdisk in assoc.associators("Win32_DiskPartitionToDiskDrive"):
                        return pdisk.DeviceID  # e.g. \\.\PHYSICALDRIVE0
    except Exception:
        pass
    return None


def _normalize_physical_drive_name(device_id):
    """Normalize a Windows physical drive ID to a consistent name.

    Input: '\\\\.\\PHYSIVE0' or '\\\\.\\PhysicalDrive0'
    Output: 'PhysicalDrive0'
    """
    import re
    m = re.search(r'(PhysicalDrive\d+)', device_id)
    if m:
        return m.group(1)
    return device_id


def _get_windows_disks_wmi():
    """Fallback: get disk partitions via WMI when psutil returns empty results.

    Returns a list of dicts with device, mountpoint, fstype — same format
    as psutil.disk_partitions() entries.
    """
    try:
        import wmi
        c = wmi.WMI()
        disks = []
        for disk in c.Win32_LogicalDisk(DriveType=3):
            # DriveType=3 = Local Disk (fixed HDD/SSD)
            disks.append(type('Partition', (), {
                'device': disk.DeviceID + '\\',
                'mountpoint': disk.DeviceID + '\\',
                'fstype': disk.FileSystem or '',
                'opts': '',
            })())
        return disks
    except Exception:
        return []


def collect_storage():
    """Collect all storage metrics per disk: capacity, usage, temp, smart,
    plus disk I/O counters (throughput, IOPS, utilization).

    I/O counters are per-physical-disk. All partitions on the same physical
    disk share the same I/O counters. Counters are cumulative; deltas are
    computed server-side during ingest.
    """
    try:
        import psutil
        disks = []
        # Get per-physical-disk I/O counters once
        disk_io = _get_windows_disk_io() if platform.system() == 'Windows' else {}
        # Build drive-to-physical map once for all partitions
        drive_map = _get_drive_to_physical_map() if platform.system() == 'Windows' and disk_io else {}
        partitions = psutil.disk_partitions()
        # On Windows, if psutil returns no partitions, fall back to WMI
        if platform.system() == 'Windows' and not partitions:
            partitions = _get_windows_disks_wmi()
        for part in partitions:
            # Skip virtual/special filesystems on non-Windows
            if platform.system() != 'Windows':
                if part.fstype in ('squashfs', 'tmpfs', 'devtmpfs'):
                    continue
            # On Windows, skip CD-ROM and removable drives with no media
            if platform.system() == 'Windows':
                if part.fstype == '' or part.fstype.lower() == 'cdfs':
                    continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                disk = {
                    'device': part.device,
                    'mountpoint': part.mountpoint,
                    'fstype': part.fstype,
                    'capacity_bytes': usage.total,
                    'usage_pct': round(usage.percent, 1),
                    'temp_c': None,
                    'smart_health': '',
                }
                # Attach I/O counters using pre-built drive map (Windows)
                if platform.system() == 'Windows' and drive_map:
                    drive_letter = part.device[0].upper()
                    phys_name = drive_map.get(drive_letter)
                    if phys_name:
                        io = disk_io.get(phys_name, {})
                        disk['read_bytes'] = io.get('read_bytes')
                        disk['write_bytes'] = io.get('write_bytes')
                        disk['read_iops'] = io.get('read_iops')
                        disk['write_iops'] = io.get('write_iops')
                        disk['busy_time_ms'] = io.get('busy_time_ms')
                        logging.getLogger('storage').debug(
                            'I/O for %s mapped to %s', part.device, phys_name)
                    else:
                        logging.getLogger('storage').debug(
                            'No physical drive mapping for %s, I/O not attached', part.device)
                elif platform.system() == 'Linux':
                    try:
                        out = subprocess.run(
                            ['sudo', 'smartctl', '-a', part.device],
                            capture_output=True, text=True, timeout=5
                        )
                        for line in out.stdout.splitlines():
                            if 'Temperature' in line and 'Celsius' in line:
                                parts_w = line.split()
                                for i, w in enumerate(parts_w):
                                    if w.replace('.', '').isdigit() and i > 0:
                                        disk['temp_c'] = float(w)
                                        break
                    except Exception:
                        pass
                else:
                    disk['device'] = _get_physical_disk_name(part.device)
                    disk['smart_health'] = 'unsupported'
                disks.append(disk)
            except PermissionError:
                logging.getLogger('storage').debug('Permission denied for %s, skipping', part.device)
                continue
            except OSError as e:
                logging.getLogger('storage').debug('OS error for %s: %s, skipping', part.device, e)
                continue
        return disks
    except Exception as e:
        logging.getLogger('storage').warning('Storage collection failed: %s', e)
        return []


def _get_physical_drive_for_partition(partition_letter):
    """Map a Windows partition letter to a physical drive number using WMI."""
    try:
        import wmi
        c = wmi.WMI()
        drive_letter = partition_letter[0].upper()
        for ld in c.Win32_LogicalDisk(DriveType=3):
            if ld.DeviceID.startswith(drive_letter):
                for assoc in ld.associators("Win32_LogicalDiskToPartition"):
                    for pdisk in assoc.associators("Win32_DiskPartitionToDiskDrive"):
                        return pdisk.DeviceID  # e.g. \\.\PHYSICALDRIVE0
    except Exception:
        pass
    return None


def _read_smart_windows(physical_drive):
    """Read SMART health status from a physical drive on Windows."""
    try:
        import wmi
        c = wmi.WMI(namespace='root\\wmi')
        for disk in c.MSStorageDriver_FailurePredictStatus():
            if disk.PredictFailure:
                return 'FAILING'
            else:
                return 'OK'
    except Exception:
        pass
    return None


def _get_physical_disk_name(device):
    """Get a friendly disk name on Windows."""
    try:
        import wmi
        c = wmi.WMI()
        for disk in c.Win32_DiskDrive():
            return disk.Model
    except Exception:
        pass
    return device


def collect_network():
    """Collect all network metrics per interface."""
    try:
        import psutil
        interfaces = []
        stats = psutil.net_io_counters(pernic=True)
        addrs = psutil.net_if_addrs()
        for iface, snic in stats.items():
            # Skip loopback
            if iface.lower() in ('lo', 'loopback'):
                continue
            entry = {
                'interface': iface,
                'rx_bytes': snic.bytes_recv,
                'tx_bytes': snic.bytes_sent,
                'rx_errors': snic.errin,
                'tx_errors': snic.errout,
            }
            # IPv4
            if iface in addrs:
                for a in addrs[iface]:
                    if a.family.name == 'AF_INET':
                        entry['ipv4'] = a.address
                        break
            # Skip interfaces with loopback IPv4 or no IPv4
            if not entry.get('ipv4') or entry['ipv4'].startswith('127.'):
                continue
            # Link speed on Windows via WMI
            if platform.system() == 'Windows':
                try:
                    import wmi
                    c = wmi.WMI()
                    for nic in c.Win32_NetworkAdapter(NetEnabled=True):
                        for nic_config in nic.associators("Win32_NetworkAdapterSetting"):
                            pass
                        if nic.Speed:
                            entry['link_speed_mbps'] = int(nic.Speed) // 1_000_000
                            break
                except Exception:
                    pass
            else:
                try:
                    speed_path = Path(f'/sys/class/net/{iface}/speed')
                    speed = int(speed_path.read_text().strip())
                    if speed > 0:
                        entry['link_speed_mbps'] = speed
                except Exception:
                    pass
            interfaces.append(entry)
        return interfaces
    except Exception as e:
        logging.getLogger('network').warning('Network collection failed: %s', e)
        return []


def collect_gpus():
    """Collect all GPU metrics: uuid, model, memory, utilization, temp, fan, power."""
    try:
        import pynvml
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        gpus = []
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            try:
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
            except Exception:
                temp = None
            try:
                fan = pynvml.nvmlDeviceGetFanSpeed(handle)
            except Exception:
                fan = None
            try:
                power = pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0
                power_limit = pynvml.nvmlDeviceGetPowerManagementDefaultLimit(handle) / 1000.0
            except Exception:
                power = None
                power_limit = None

            # Collect PCIe link info
            pcie_current_gen = None
            pcie_max_gen = None
            pcie_current_width = None
            pcie_max_width = None
            try:
                pcie_current_gen = pynvml.nvmlDeviceGetCurrPcieLinkGeneration(handle)
                pcie_max_gen = pynvml.nvmlDeviceGetMaxPcieLinkGeneration(handle)
                pcie_current_width = pynvml.nvmlDeviceGetCurrPcieLinkWidth(handle)
                pcie_max_width = pynvml.nvmlDeviceGetMaxPcieLinkWidth(handle)
            except Exception:
                pass  # PCIe info not available on all GPUs/systems

            # Collect GPU clock speeds
            gpu_core_clock_mhz = None
            gpu_mem_clock_mhz = None
            try:
                gpu_core_clock_mhz = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_GRAPHICS)
                gpu_mem_clock_mhz = pynvml.nvmlDeviceGetClockInfo(handle, pynvml.NVML_CLOCK_MEM)
            except Exception as e:
                logging.getLogger('gpu').debug('GPU %d clock info not available: %s', i, e)

            gpus.append({
                'uuid': pynvml.nvmlDeviceGetUUID(handle),
                'model': pynvml.nvmlDeviceGetName(handle),
                'mem_total_mb': info.total // (1024 * 1024),
                'mem_used_mb': info.used // (1024 * 1024),
                'mem_free_mb': info.free // (1024 * 1024),
                'mem_util_pct': round(info.used / info.total * 100, 1) if info.total else None,
                'gpu_util_pct': util.gpu,
                'temp_c': temp,
                'fan_speed_pct': fan,
                'power_draw_w': power,
                'power_limit_w': power_limit,
                'pcie_current_gen': pcie_current_gen,
                'pcie_max_gen': pcie_max_gen,
                'pcie_current_width': pcie_current_width,
                'pcie_max_width': pcie_max_width,
                'gpu_core_clock_mhz': gpu_core_clock_mhz,
                'gpu_mem_clock_mhz': gpu_mem_clock_mhz,
            })
        pynvml.nvmlShutdown()
        return gpus
    except Exception as e:
        logging.getLogger('gpu').warning('GPU collection failed: %s', e)
        return []


def collect_gpu_processes():
    """Collect GPU process list from nvidia-smi (Windows version).

    Same parsing as Linux — nvidia-smi output format is identical
    across platforms for the Processes section.
    """
    processes = []
    try:
        out = subprocess.run(
            ['nvidia-smi'],
            capture_output=True, text=True, timeout=10
        )
        if out.returncode != 0:
            return processes

        in_processes = False
        for line in out.stdout.splitlines():
            stripped = line.strip()

            if stripped.startswith('| Processes:'):
                in_processes = True
                continue

            if not in_processes:
                continue

            if stripped.startswith('+') or not stripped:
                if processes:
                    break
                continue

            if '---' in stripped or ('GPU' in stripped and 'PID' in stripped):
                continue

            if 'ID' in stripped and 'Usage' in stripped and not stripped.startswith('|'):
                continue

            clean = stripped.replace('|', '').strip()
            if not clean:
                continue
            parts = clean.split()

            if len(parts) < 5:
                continue

            try:
                gpu_idx = int(parts[0])
                pid = int(parts[3])
                proc_type = parts[4]
                gpu_mem_str = parts[-1] if len(parts) >= 6 else 'N/A'
                proc_name = ' '.join(parts[5:-1]) if len(parts) >= 6 else ''

                gpu_mem_mb = None
                if gpu_mem_str not in ('N/A', ''):
                    mem_val = gpu_mem_str.replace('MiB', '').replace('GiB', '').strip()
                    try:
                        gpu_mem_mb = int(float(mem_val))
                        if 'GiB' in gpu_mem_str:
                            gpu_mem_mb = int(gpu_mem_mb * 1024)
                    except ValueError:
                        pass

                processes.append({
                    'gpu_index': gpu_idx,
                    'pid': pid,
                    'type': proc_type,
                    'name': proc_name,
                    'gpu_mem_mb': gpu_mem_mb,
                })
            except (ValueError, IndexError):
                continue
    except Exception as e:
        logging.getLogger('gpu_processes').warning('GPU process collection failed: %s', e)
    return processes


def collect_docker():
    """Collect Docker container metrics using docker CLI via subprocess.

    Tries multiple approaches to access Docker:
    1. Direct 'docker' CLI (default on Windows with Docker Desktop)
    2. Returns empty list if it fails

    Uses subprocess calls instead of the Docker Python SDK because:
    1. The SDK requires direct access to the Docker socket/named pipe
    2. The CLI approach works reliably on both Windows and Linux
    3. No Python SDK dependency needed

Note: Per-container CPU/memory usage is NOT collected because:
    - 'docker stats' requires elevated permissions and is unreliable
    - GPU compute metrics are covered by NVIDIA GPU collection
    For each container, collects: container_id, name, image, status, created, status_text.
    """
    containers = []

    # On Windows, Docker Desktop manages permissions differently
    # The user running the agent should have Docker access by default
    # Try direct docker CLI access
    docker_prefix = ['docker']

    # Quick test if docker is accessible
    try:
        test = subprocess.run(
            docker_prefix + ['ps', '-a', '--format', '{{.ID}}'],
            capture_output=True, text=True, timeout=10,
            encoding='utf-8', errors='replace'
        )
    except (FileNotFoundError, OSError):
        logging.getLogger('docker').warning(
            'Docker collection failed: docker CLI not found. '
            'Ensure Docker Desktop is installed and docker is in PATH.'
        )
        return []
    if test.returncode != 0:
        logging.getLogger('docker').warning(
            'Docker collection failed: cannot access docker CLI. '
            'Ensure Docker Desktop is running and the user has permissions. '
            f'Error: {test.stderr.strip()[:200]}'
        )
        return []

    try:
        # Step 1: Get list of all containers (including stopped ones)
        # Format: ID|Names|Image|Status|CreatedAt
        result = subprocess.run(
            docker_prefix + ['ps', '-a', '--no-trunc',
             '--format', '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.CreatedAt}}'],
            capture_output=True, text=True, timeout=15,
            encoding='utf-8', errors='replace'
        )
        if result.returncode != 0:
            logging.getLogger('docker').warning(
                'docker ps failed (exit %d): %s',
                result.returncode, result.stderr.strip()[:200]
            )
            return []

        lines = [l for l in result.stdout.strip().split('\n') if l]
        if not lines:
            return []

        for line in lines:
            parts = line.split('|', 4)
            if len(parts) < 5:
                continue
            cid, name, image, status_str, created_str = parts
            container_id = cid[:12]

            # Parse status
            status = 'running' if status_str.startswith('Up') else 'exited'
            if 'Restarting' in status_str:
                status = 'restarting'

            containers.append({
                'container_id': container_id,
                'name': name,
                'image': image,
                'status': status,
                'created': created_str.strip(),
                'status_text': status_str.strip(),
            })

        return containers

    except subprocess.TimeoutExpired:
        logging.getLogger('docker').warning('Docker collection timed out')
        return []
    except FileNotFoundError:
        logging.getLogger('docker').warning('docker CLI not found')
        return []
    except Exception as e:
        logging.getLogger('docker').warning('Docker collection failed: %s', e)
        return []


def collect_top_processes(limit=20):
    """Collect top processes by CPU and memory usage.

    Windows: uses psutil with cpu_percent sampling.
    Linux: handled by agent/run.py version.

    Returns dict with:
      - by_cpu: top N processes sorted by CPU% (descending)
      - by_mem: top N processes sorted by memory% (descending)
      - total_count: total number of running processes
    """
    try:

        if platform.system() == 'Windows':
            # Windows: use psutil for process collection
            import psutil
            attrs = ['pid', 'name', 'memory_percent', 'username', 'cmdline']
            procs = []
            for p in psutil.process_iter(attrs):
                info = p.info
                cmdline = info.get('cmdline')
                info['cmdline'] = ' '.join(cmdline)[:200] if cmdline else ''
                info['cpu_pct'] = 0.0  # Will be populated below
                info['mem_pct'] = info.get('memory_percent', 0.0)
                info['status'] = ''
                procs.append(info)

            # Get CPU% using psutil with a single system-wide call first
            # Then per-process with short interval for top consumers
            try:
                # First pass: quick cpu_percent (returns 0.0 first time)
                for p in procs[:50]:  # Only top 50 to avoid timeout
                    try:
                        proc = psutil.Process(p['pid'])
                        p['cpu_pct'] = proc.cpu_percent(interval=0.05)
                    except Exception:
                        pass
            except Exception:
                pass

            by_mem = sorted(procs, key=lambda x: x.get('mem_pct', 0), reverse=True)[:limit]
            by_cpu = sorted(procs, key=lambda x: x.get('cpu_pct', 0), reverse=True)[:limit]

            return {
                'by_cpu': by_cpu,
                'by_mem': by_mem,
                'total_count': len(procs),
            }
        else:
            # Linux: use top -bn1 for accurate CPU%
            out = subprocess.run(
                ['top', '-bn1'],
                capture_output=True, text=True, timeout=15
            )
            if out.returncode != 0:
                return None

            procs = []
            in_tasks = False
            for line in out.stdout.splitlines():
                if re.match(r'^\s*PID\s+USER\s+PR\s+NI\s+VIRT\s+RES\s+SHR\s+S\s+%CPU\s+', line):
                    in_tasks = True
                    continue
                if not in_tasks:
                    continue
                if not line.strip():
                    break

                parts = line.split(None, 11)
                if len(parts) < 12:
                    continue
                try:
                    pid = int(parts[0])
                    username = parts[1]
                    status = parts[8]
                    cpu_pct = float(parts[9])
                    mem_pct = float(parts[10])
                    cmdline = parts[11] if len(parts) > 11 else ''

                    name = cmdline.split()[0] if cmdline else ''
                    if '/' in name:
                        name = name.rsplit('/', 1)[-1]
                    name = name[:30]

                    procs.append({
                        'pid': pid,
                        'name': name,
                        'cpu_pct': cpu_pct,
                        'mem_pct': mem_pct,
                        'username': username,
                        'cmdline': cmdline[:200],
                        'status': status,
                    })
                except (ValueError, IndexError):
                    continue

            if not procs:
                return None

            by_cpu = sorted(procs, key=lambda x: x['cpu_pct'], reverse=True)[:limit]
            by_mem = sorted(procs, key=lambda x: x['mem_pct'], reverse=True)[:limit]

            return {
                'by_cpu': by_cpu,
                'by_mem': by_mem,
                'total_count': len(procs),
            }
    except Exception as e:
        logging.getLogger('processes').warning('Process collection failed: %s', e)
        return None


def collect_software():
    """Collect software/OS info."""
    result = {
        'hostname': platform.node(),
        'os_distro': platform.platform(),
        'kernel': platform.release(),
    }
    try:
        import psutil
        result['uptime_s'] = int(time.time() - psutil.boot_time())
    except Exception:
        pass
    # NVIDIA driver
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=driver_version', '--format=csv,noheader'],
                           capture_output=True, text=True, timeout=5,
                           encoding='utf-8', errors='replace')
        if out.returncode == 0:
            result['nvidia_driver'] = out.stdout.strip().split('\n')[0]
    except Exception:
        pass
    # Docker version
    try:
        out = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'],
                           capture_output=True, text=True, timeout=5,
                           encoding='utf-8', errors='replace')
        if out.returncode == 0:
            result['docker_version'] = out.stdout.strip()
    except Exception:
        pass
    return result


def collect_errors():
    """Collect recent system errors from Windows Event Log or journalctl."""
    errors = []
    if platform.system() == 'Windows':
        # Use Windows Event Log via PowerShell
        try:
            ps_cmd = (
                "Get-WinEvent -FilterHashtable @{"
                "LogName='Application','System';"
                "Level=1,2,3;"
                "StartTime=(Get-Date).AddMinutes(-5)"
                "} -MaxEvents 20 -ErrorAction SilentlyContinue | "
                "ForEach-Object { $_.TimeCreated.ToString('yyyy-MM-ddTHH:mm:ss') + ' ' + "
                "$_.ProviderName + ': ' + $_.Message }"
            )
            out = subprocess.run(
                ['powershell', '-Command', ps_cmd],
                capture_output=True, text=True, timeout=15,
                encoding='utf-8', errors='replace'
            )
            if out.returncode == 0 and out.stdout and out.stdout.strip():
                seen = set()
                for line in out.stdout.strip().splitlines():
                    if line not in seen and len(line.strip()) > 0:
                        seen.add(line)
                        parts = line.split(None, 2)
                        errors.append({
                            'source': parts[1].rstrip(':') if len(parts) > 1 else 'windows',
                            'message': line[:200],
                            'timestamp': parts[0] if len(parts) > 0 else '',
                        })
        except Exception as e:
            logging.getLogger('errors').warning('Windows Event Log collection failed: %s', e)
    else:
        # Linux: use journalctl
        try:
            out = subprocess.run(
                ['journalctl', '-p', 'err..crit', '--since', '5 min ago', '--no-pager', '-o', 'short-iso'],
                capture_output=True, text=True, timeout=10
            )
            seen = set()
            for line in out.stdout.strip().splitlines()[:20]:
                if line not in seen:
                    seen.add(line)
                    errors.append({
                        'source': 'kernel',
                        'message': line[:200],
                        'timestamp': line[:23] if len(line) > 23 else '',
                    })
        except Exception as e:
            logging.getLogger('errors').warning('Error collection failed: %s', e)
    return errors


# ── Payload & Transport ─────────────────────────────────────────────────────

def build_payload(config):
    """Build the telemetry payload.

    Payload structure (no duplication):
    - metrics: all data in one section. Each collector returns both static
              identifiers (model, uuid, capacity) and dynamic values
              (utilization, temp, usage). This ensures complete data for
              per-minute historical tracking. GPU uuid is included so
              replacements can be tracked accurately.
    - motherboard: static hardware info
    - software: OS-level info (hostname, kernel, driver versions)
    - errors: recent system errors
    """
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    metrics = {
        'cpu': collect_cpu(),
        'memory': collect_memory(),
        'storage': collect_storage(),
        'network': collect_network(),
        'gpus': collect_gpus(),
        'gpu_processes': collect_gpu_processes(),
        'docker_containers': collect_docker(),
        'top_processes': collect_top_processes(),
    }

    payload = {
        'rig_uuid': config['rig_uuid'],
        'rig_name': config.get('rig_name', ''),
        'schema_version': __schema_version__,
        'agent_version': __version__,
        'timestamp': now,
        'metrics': metrics,
        'motherboard': collect_motherboard(),
        'software': collect_software(),
        'errors': collect_errors(),
    }

    return payload


def send_payload(config, payload):
    """Send payload to server with retry logic."""
    import time
    import random

    data = json.dumps(payload).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': config['api_key'],
        'X-Rig-UUID': config['rig_uuid'],
        'User-Agent': f'rig-monitor-agent/{__version__}',
    }

    max_retries = config.get('retry_attempts', 3)
    timeout = (3.0, 10.0)

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"{config['server_endpoint']}/api/v1/ingest/",
                data=data,
                headers=headers,
                timeout=timeout,
            )
            logging.getLogger('transport').info(
                'Ingest response: %d %s', resp.status_code, resp.text[:100]
            )
            return resp.status_code, resp.json() if resp.content else {}
        except requests.exceptions.RequestException as e:
            logging.getLogger('transport').warning('Attempt %d failed: %s', attempt + 1, e)
            if attempt < max_retries - 1:
                delay = (2 ** attempt) + random.uniform(0, 0.4)
                time.sleep(delay)

    logging.getLogger('transport').error('All %d attempts failed', max_retries)
    return None, {}


# ── Acquisition Lock ────────────────────────────────────────────────────────

class AcquisitionLock:
    """Cross-platform file lock to prevent overlapping agent runs."""

    def __init__(self):
        self._lock_file = None
        self._locked = False

    def acquire(self):
        """Try to acquire the lock. Returns True if successful."""
        lock_dir = Path(__file__).resolve().parent / 'logs'
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / '.agent.lock'

        try:
            if platform.system() == 'Windows':
                import msvcrt
                self._lock_file = open(lock_path, 'w')
                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                self._locked = True
                return True
            else:
                import fcntl
                self._lock_file = open(lock_path, 'w')
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._locked = True
                return True
        except (IOError, OSError):
            if self._lock_file:
                self._lock_file.close()
                self._lock_file = None
            return False

    def release(self):
        """Release the lock."""
        if self._lock_file and self._locked:
            try:
                if platform.system() == 'Windows':
                    import msvcrt
                    self._lock_file.seek(0)
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                self._lock_file.close()
                self._lock_file = None
                self._locked = False


# ── Windows Task Scheduler Setup Helper ─────────────────────────────────────

def print_task_scheduler_instructions():
    """Print instructions for setting up Windows Task Scheduler."""
    script_path = Path(__file__).resolve()
    python_path = sys.executable

    pythonw_path = python_path.replace('python.exe', 'pythonw.exe')
    if not Path(pythonw_path).exists():
        pythonw_path = python_path.replace('python3.exe', 'pythonw.exe')
    if not Path(pythonw_path).exists():
        pythonw_path = python_path

    print()
    print("=" * 60)
    print("  Windows Task Scheduler Setup")
    print("=" * 60)
    print()
    print("The agent should run every 60 seconds.")
    print("Windows Task Scheduler minimum interval is 1 minute.")
    print("The built-in --install-task flag uses 1-minute intervals.")
    print("NOTE: Uses pythonw.exe to run without a visible terminal window.")
    print()
    print("Option 1 — Automatic (run as Administrator):")
    print(f'  python "{script_path}" --install-task')
    print()
    print("Option 2 — Using schtasks command (run as Administrator):")
    print(f'  schtasks /create /tn "GPURigMonitorAgent" '
          f'/tr "\\{pythonw_path}\\" \\"{script_path}\\"" '
          f'/sc minute /mo 1 /f')
    print()
    print("Option 3 — Using Task Scheduler GUI:")
    print("  1. Open Task Scheduler (taskschd.msc)")
    print("  2. Click 'Create Basic Task'")
    print("  3. Name: GPURigMonitorAgent")
    print("  4. Trigger: When the computer starts")
    print("  5. Action: Start a program")
    print(f"  6. Program: {pythonw_path}")
    print('  7. Arguments: "{script_path}"')
    print("  8. Check 'Run whether user is logged on or not'")
    print("  9. Check 'Run with highest privileges' (for SMART/GPU access)")
    print()
    print("To verify: schtasks /query /tn GPURigMonitorAgent")
    print("To remove: schtasks /delete /tn GPURigMonitorAgent /f")
    print()
    print("=" * 60)


def create_windows_task():
    """Attempt to create a Windows Task Scheduler entry automatically."""
    if platform.system() != 'Windows':
        print("This function is only available on Windows.")
        return

    script_path = Path(__file__).resolve()
    python_path = sys.executable

    pythonw_path = python_path.replace('python.exe', 'pythonw.exe')
    if not Path(pythonw_path).exists():
        pythonw_path = python_path.replace('python3.exe', 'pythonw.exe')
    if not Path(pythonw_path).exists():
        pythonw_path = python_path

    task_name = "GPURigMonitorAgent"
    arguments = f'"{pythonw_path}" "{script_path}"'

    cmd = [
        'schtasks', '/create',
        '/tn', task_name,
        '/tr', arguments,
        '/sc', 'minute',
        '/mo', '1',
        '/ru', 'SYSTEM',
        '/f',
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"Task '{task_name}' created successfully.")
            print("The agent will run every 1 minute (hidden window).")
            print(f"To verify: schtasks /query /tn {task_name}")
            print(f"To remove: schtasks /delete /tn {task_name} /f")
        else:
            print(f"Failed to create task: {result.stderr}")
            print("Try running as Administrator, or set up manually:")
            print_task_scheduler_instructions()
    except Exception as e:
        print(f"Error creating task: {e}")
        print_task_scheduler_instructions()


def remove_windows_task():
    """Remove the Windows Task Scheduler entry."""
    if platform.system() != 'Windows':
        print("This function is only available on Windows.")
        return

    task_name = "GPURigMonitorAgent"
    try:
        result = subprocess.run(
            ['schtasks', '/delete', '/tn', task_name, '/f'],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"Task '{task_name}' removed successfully.")
        else:
            print(f"Failed to remove task: {result.stderr}")
    except Exception as e:
        print(f"Error removing task: {e}")


def _detect_server():
    """Try to auto-detect the server on common local/test IP ranges."""
    import socket

    print()
    print("=" * 60)
    print("  Server Auto-Detection")
    print("=" * 60)
    print()

    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
        print(f"  This machine: {hostname} ({local_ip})")
    except Exception:
        print(f"  This machine: {hostname}")

    candidates = [
        ("192.168.253.1", "VMware NAT (VMnet8) gateway"),
        ("192.168.40.1", "VMware Host-Only (VMnet1) gateway"),
        ("192.168.8.1", "Common gateway (192.168.8.x)"),
        ("192.168.0.1", "Common gateway (192.168.0.x)"),
        ("192.168.1.1", "Common gateway (192.168.1.x)"),
        ("127.0.0.1", "Localhost (same machine)"),
    ]

    try:
        local_parts = local_ip.rsplit('.', 1)
        if len(local_parts) == 2:
            subnet_gw = f"{local_parts[0]}.1"
            candidates.insert(0, (subnet_gw, f"Detected gateway (subnet {local_parts[0]}.x)"))
    except Exception:
        pass

    print("  Probing candidates...")
    found = []
    for ip, label in candidates:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((ip, 80))
            sock.close()
            if result == 0:
                print(f"  {ip:20s} — {label} (port 80 open)")
                found.append(ip)
            else:
                print(f"  {ip:20s} — {label} (no response)")
        except Exception:
            print(f"  {ip:20s} — {label} (error)")

    if found:
        best = found[0]
        print()
        print(f"  Recommended server_endpoint: http://{best}")
        print(f"  Or try: https://{best}")
        if best == "192.168.253.1":
            print()
            print("  NOTE: This is the VMware NAT gateway. The VM itself may use")
            print("  a different IP like 192.168.253.xxx. Try probing higher IPs:")
            for i in range(128, 140):
                probe = f"192.168.253.{i}"
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    r = s.connect_ex((probe, 80))
                    s.close()
                    if r == 0:
                        print(f"  {probe} responds on port 80 — try: http://{probe}")
                except Exception:
                    pass
    else:
        print()
        print("  No server found on common addresses.")
        print("  Check that the server is running and the IP is correct.")

    print()
    print("=" * 60)


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    # Handle command-line arguments for task management and diagnostics
    if '--install-task' in sys.argv:
        create_windows_task()
        return
    if '--remove-task' in sys.argv:
        remove_windows_task()
        return
    if '--help-task' in sys.argv:
        print_task_scheduler_instructions()
        return
    if '--detect-server' in sys.argv:
        _detect_server()
        return

    config = load_config()
    setup_logging(debug=config.get('debug_mode', False))
    logger = logging.getLogger('main')

    # Random jitter to spread load across the reporting interval
    jitter_s = random.uniform(0, 25)
    time.sleep(jitter_s)

    # Acquire lock to prevent overlapping runs
    lock = AcquisitionLock()
    if not lock.acquire():
        logger.warning('Another instance is already running. Exiting.')
        sys.exit(0)

    try:
        logger.info('Starting collection for rig %s', config['rig_uuid'])
        payload = build_payload(config)
        log_payload(payload)
        status_code, response = send_payload(config, payload)
        if status_code in (200, 202):
            logger.info('Payload accepted: %s', response.get('status', 'unknown'))
        else:
            logger.error('Payload rejected: %s %s', status_code, response)
    except Exception as e:
        logger.exception('Unexpected error: %s', e)
        sys.exit(1)
    finally:
        lock.release()


if __name__ == '__main__':
    main()
