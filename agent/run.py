#!/usr/bin/env python3
"""
GPU Rig Monitoring Agent

Collects hardware/software metrics and sends them to the monitoring server.
Designed to run via cron every 60 seconds.

Usage:
    python3 run.py

Config file: /etc/monitoring-agent/config.yaml

Versioning:
    - __version__ (MAJOR.MINOR.PATCH): incremented for agent-side changes
      (collectors, payload format, bug fixes).
    - __schema_version__ (MAJOR.MINOR): incremented when payload structure
      changes in a way that affects server serialization/storage.

    After making changes to agent code, you MUST increment __version__ and/or
    __schema_version__ according to the depth of changes:
    - PATCH: bug fixes, minor collector tweaks (e.g. 1.4.0 → 1.4.1)
    - MINOR: new collectors, new payload fields (e.g. 1.4.0 → 1.5.0)
    - MAJOR: breaking changes to payload structure (e.g. 1.4 → 2.0)

    See docs/GPU_Rig_Monitoring_Architecture.md §3.1a for full versioning rules.
"""

import os
import sys
import json
import re
import signal
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

__version__ = '1.5.9'
__schema_version__ = '1.7'

# ── Config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG_PATH = '/etc/monitoring-agent/config.yaml'

def load_config(path=DEFAULT_CONFIG_PATH):
    """Load and validate configuration."""
    with open(path, 'r') as f:
        config = yaml.safe_load(f) or {}

    required = ['api_key', 'server_endpoint']
    for field in required:
        if not config.get(field):
            print(f"ERROR: Missing required config field: {field}", file=sys.stderr)
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
    log_dir = Path('/var/log/monitoring-agent')
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        # Fallback to /tmp if /var/log is not writable (e.g., during testing)
        log_dir = Path('/tmp/monitoring-agent')
        log_dir.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if debug else logging.INFO
    fmt = '{"ts":"%(asctime)s","level":"%(levelname)s","module":"%(name)s","msg":"%(message)s"}'

    try:
        handler = logging.handlers.RotatingFileHandler(
            log_dir / 'agent.log', maxBytes=10*1024*1024, backupCount=3
        )
        handler.setFormatter(logging.Formatter(fmt))
    except PermissionError:
        # If we can't write to the log file, just use console
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(fmt))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt))

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    root.addHandler(console)


def log_payload(payload):
    """Save the latest full JSON payload to payload.json for local analysis."""
    log_dir = Path('/var/log/monitoring-agent')
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        payload_path = log_dir / 'payload.json'
        payload_path.write_text(json.dumps(payload, indent=2, default=str) + '\n')
    except PermissionError:
        pass  # Silently skip if we can't write (e.g., testing as non-root)


# ── Metric Collectors (all-in-one, no duplication) ─────────────────────────

def collect_cpu():
    """Collect all CPU metrics: static info + time-series data."""
    try:
        import psutil
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count_phys = psutil.cpu_count(logical=False)
        cpu_count_log = psutil.cpu_count(logical=True)
        load_avg = os.getloadavg()

        temp_c = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                # Strategy: find the highest temperature among CPU core sensors.
                # Priority:
                #   1. Known CPU sensor names (coretemp, k10temp) — take highest core temp
                #   2. Any sensor with "Core" in label — take highest
                #   3. Fallback: first available reading
                cpu_sensor_names = ('coretemp', 'k10temp')
                best_temp = None

                # First pass: known CPU sensors
                for name in cpu_sensor_names:
                    if name in temps:
                        for entry in temps[name]:
                            if entry.current is not None:
                                if best_temp is None or entry.current > best_temp:
                                    best_temp = entry.current

                # Second pass: any entry with "Core" in label
                if best_temp is None:
                    for name, entries in temps.items():
                        for entry in entries:
                            if entry.current is not None and 'Core' in entry.label:
                                if best_temp is None or entry.current > best_temp:
                                    best_temp = entry.current

                # Third pass: any temperature reading at all
                if best_temp is None:
                    for name, entries in temps.items():
                        if entries and entries[0].current is not None:
                            best_temp = entries[0].current
                            break

                temp_c = best_temp
        except Exception:
            pass

        model = 'Unknown'
        try:
            import cpuinfo
            info = cpuinfo.get_cpu_info()
            model = info.get('brand_raw', 'Unknown')
        except Exception:
            pass

        return {
            'model': model,
            'physical_cores': cpu_count_phys,
            'logical_cores': cpu_count_log,
            'load_avg': list(load_avg),
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
    """Collect motherboard info."""
    result = {}
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


def _get_disk_io_counters():
    """Get per-physical-disk I/O counters from psutil.

    Returns a dict keyed by disk name (e.g. 'sda', 'nvme0n1') with:
      read_bytes, write_bytes, read_iops, write_iops, busy_time_ms
    Filters out partitions (e.g. sda1, sda2) and virtual devices (loop, sr, dm).
    """
    try:
        import psutil
        io = psutil.disk_io_counters(perdisk=True)
        if not io:
            return {}
    except Exception:
        return {}

    # Identify whole-disk devices (not partitions)
    # Whole disks: sda, nvme0n1, vda, hda (no trailing digits, or nvme pattern)
    # Partitions: sda1, sda2, nvme0n1p1 (have trailing digits after base name)
    import re
    whole_disks = {}
    for name, counters in io.items():
        # Skip loop devices, sr (CD-ROM), dm (device mapper virtual)
        if name.startswith('loop') or name.startswith('sr') or name.startswith('dm-'):
            continue
        # Skip partitions: names ending in digits that aren't pure NVMe
        # NVMe whole disk: nvme0n1 (ends in digit but has 'p' before it for partitions)
        # SATA whole disk: sda, vda, hda (no digits at all)
        # Partition: sda1, nvme0n1p1
        if re.match(r'^[a-z]+\d+$', name) and not name.startswith('nvme'):
            # Ends in digits, not NVMe → partition (sda1, vda2, etc.)
            continue
        if re.match(r'^nvme\dn\d+p\d+$', name):
            # NVMe partition: nvme0n1p1
            continue
        whole_disks[name] = {
            'read_bytes': counters.read_bytes,
            'write_bytes': counters.write_bytes,
            'read_iops': counters.read_count,
            'write_iops': counters.write_count,
            'busy_time_ms': getattr(counters, 'busy_time', None),
        }
    return whole_disks


def _disk_to_whole_disk(device_name):
    """Map a partition device to its parent whole-disk device.

    Examples:
        /dev/sda1 -> sda
        /dev/sda  -> sda
        /dev/nvme0n1p1 -> nvme0n1
        /dev/nvme0n1 -> nvme0n1
    """
    import re
    # Strip /dev/ prefix
    name = device_name.split('/')[-1]
    # NVMe: nvme0n1p1 -> nvme0n1, nvme0n1 -> nvme0n1
    nvme_match = re.match(r'^(nvme\dn\d+)', name)
    if nvme_match:
        return nvme_match.group(1)
    # SATA/SCSI: sda1 -> sda, vda2 -> vda, sda -> sda
    sat_match = re.match(r'^([a-z]+)', name)
    if sat_match:
        return sat_match.group(1)
    return name


def collect_storage():
    """Collect all storage metrics per disk: capacity, usage, temp, smart,
    plus disk I/O counters (throughput, IOPS, utilization).

    I/O counters are per-physical-disk (whole disk, not partition).
    All partitions on the same physical disk share the same I/O counters.
    Counters are cumulative; deltas are computed server-side during ingest.
    """
    try:
        import psutil
        disks = []
        # Get per-physical-disk I/O counters once, reuse for all partitions
        disk_io = _get_disk_io_counters()
        for part in psutil.disk_partitions():
            if part.fstype in ('squashfs', 'tmpfs', 'devtmpfs'):
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
                whole_disk = _disk_to_whole_disk(part.device)
                io = disk_io.get(whole_disk, {})
                disk = {
                    'device': part.device,
                    'mountpoint': part.mountpoint,
                    'fstype': part.fstype,
                    'capacity_bytes': usage.total,
                    'usage_pct': round(usage.percent, 1),
                    'temp_c': None,
                    'smart_health': '',
                    # Disk I/O counters (cumulative, per-physical-disk)
                    'read_bytes': io.get('read_bytes'),
                    'write_bytes': io.get('write_bytes'),
                    'read_iops': io.get('read_iops'),
                    'write_iops': io.get('write_iops'),
                    'busy_time_ms': io.get('busy_time_ms'),
                }
                # Try SMART for temperature
                try:
                    out = subprocess.run(
                        ['sudo', 'smartctl', '-a', part.device],
                        capture_output=True, text=True, timeout=5
                    )
                    for line in out.stdout.splitlines():
                        line_lower = line.lower()
                        if 'temperature' in line_lower:
                            # Parse temperature value from various formats:
                            # SATA: "Temperature_Celsius     0x0022   40   60   40  Old_age   Always       -       40 (Min/Max 32/62)"
                            # NVME: "Temperature:                        45 Celsius"
                            parts_w = line.split()
                            for i, w in enumerate(parts_w):
                                # Match numbers like "45", "45.0", but skip hex like "0x0022"
                                clean = w.replace('.', '').replace('-', '')
                                if clean.isdigit() and i > 0:
                                    val = float(w)
                                    # Skip unrealistic values (>150°C is likely a raw SMART value, not temp)
                                    if 0 < val <= 120:
                                        disk['temp_c'] = val
                                        break
                            if disk['temp_c'] is not None:
                                break
                except Exception:
                    pass
                # Fallback: try nvme CLI for NVMe drives
                if disk['temp_c'] is None and 'nvme' in part.device:
                    try:
                        out = subprocess.run(
                            ['sudo', 'nvme', 'smart-log', part.device],
                            capture_output=True, text=True, timeout=5
                        )
                        for line in out.stdout.splitlines():
                            if 'temperature' in line.lower():
                                # Format: "temperature : 45 Celsius" or "temperature : 318 Kelvin"
                                parts_w = line.split(':')
                                if len(parts_w) >= 2:
                                    val_str = parts_w[1].split()[0]
                                    try:
                                        val = float(val_str)
                                        # Convert Kelvin to Celsius if needed
                                        if 'kelvin' in line.lower():
                                            val = val - 273.15
                                        if 0 < val <= 120:
                                            disk['temp_c'] = round(val, 1)
                                            break
                                    except ValueError:
                                        pass
                    except Exception:
                        pass
                disks.append(disk)
            except PermissionError:
                continue
        return disks
    except Exception as e:
        logging.getLogger('storage').warning('Storage collection failed: %s', e)
        return []


def collect_network():
    """Collect all network metrics per interface."""
    try:
        import psutil
        interfaces = []
        stats = psutil.net_io_counters(pernic=True)
        addrs = psutil.net_if_addrs()
        for iface, snic in stats.items():
            # Skip loopback and common virtual interfaces
            if iface == 'lo':
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
            # Link speed
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
    """Collect GPU process list from nvidia-smi.

    Parses the full nvidia-smi output table to get all processes
    (Compute, Graphics, and Compute+Graphics).

    Returns list of dicts:
        [{gpu_index, pid, type, name, gpu_mem_mb}]
    """
    processes = []
    try:
        out = subprocess.run(
            ['nvidia-smi'], capture_output=True, text=True, timeout=10
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
    1. Direct 'docker' CLI (works if user is in docker group)
    2. 'sudo docker' CLI (works if sudoers is configured)
    3. Returns empty list if both fail

    For each container, collects: container_id, name, image, status, created, status_text.
    """
    containers = []

    # Try docker access methods: direct first, then sudo
    docker_cmds_to_try = [
        ['docker'],           # Direct access (docker group member)
        ['sudo', 'docker'],   # Sudo access (sudoers configured)
    ]

    docker_prefix = None
    for prefix in docker_cmds_to_try:
        try:
            test = subprocess.run(
                prefix + ['ps', '-a', '--format', '{{.ID}}'],
                capture_output=True, text=True, timeout=10
            )
            if test.returncode == 0:
                docker_prefix = prefix
                break
        except (FileNotFoundError, OSError):
            # Docker binary not found or not accessible — try next method
            continue

    if docker_prefix is None:
        logging.getLogger('docker').warning(
            'Docker collection failed: cannot access docker CLI '
            '(tried direct and sudo). Check docker group membership or sudoers config.'
        )
        return []

    try:
        # Step 1: Get list of all containers (including stopped ones)
        # Format: ID|Names|Image|Status|CreatedAt
        result = subprocess.run(
            docker_prefix + ['ps', '-a', '--no-trunc',
             '--format', '{{.ID}}|{{.Names}}|{{.Image}}|{{.Status}}|{{.CreatedAt}}'],
            capture_output=True, text=True, timeout=15
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

            # Parse status: "Up 2 hours" or "Exited (0) 3 hours ago" or "Restarting (1) 5 seconds ago"
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


def collect_software():
    """Collect software/OS info."""
    result = {
        'hostname': platform.node(),
        'os_distro': ' '.join(platform.dist()) if hasattr(platform, 'dist') else platform.platform(),
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
                           capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            result['nvidia_driver'] = out.stdout.strip().split('\n')[0]
    except Exception:
        pass
    # Docker version
    try:
        out = subprocess.run(['docker', 'version', '--format', '{{.Server.Version}}'],
                           capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            result['docker_version'] = out.stdout.strip()
    except Exception:
        pass
    return result


def collect_errors():
    """Collect recent system errors from journalctl."""
    errors = []
    try:
        out = subprocess.run(
            ['sudo', 'journalctl', '-p', 'err..crit', '--since', '5 min ago', '--no-pager', '-o', 'short-iso'],
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
    - metrics: all time-series data (cpu, memory, storage, network, gpu, docker).
              Each collector returns both static identifiers (model, uuid, capacity)
              and dynamic values (utilization, temp, usage). This ensures complete
              data for per-minute historical tracking.
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


# ── Main ────────────────────────────────────────────────────────────────────

def timeout_handler(signum, frame):
    raise TimeoutError("Collection exceeded time limit")


def main():
    # Random jitter to spread load across the reporting interval
    # Without this, all rigs send at the same second (cron :00)
    # causing thundering herd problem with hundreds of rigs
    jitter_s = random.uniform(0, 25)
    time.sleep(jitter_s)

    config = load_config()
    setup_logging(debug=config.get('debug_mode', False))
    logger = logging.getLogger('main')

    # Hard timeout — 30s allows collection + retries while leaving
    # margin for jitter (25s max) within the 60s cron interval
    timeout_s = config.get('collection_timeout_s', 30)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(timeout_s)

    try:
        logger.info('Starting collection for rig %s', config['rig_uuid'])
        payload = build_payload(config)
        log_payload(payload)
        status_code, response = send_payload(config, payload)
        if status_code in (200, 202):
            logger.info('Payload accepted: %s', response.get('status', 'unknown'))
        else:
            logger.error('Payload rejected: %s %s', status_code, response)
    except TimeoutError:
        logger.error('Collection timed out after %ds', timeout_s)
        sys.exit(1)
    except Exception as e:
        logger.exception('Unexpected error: %s', e)
        sys.exit(1)
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
