# GPU Rig Monitoring Agent — Linux

**Version:** 1.5.16 | **Schema:** 1.10

Linux agent for the GPU Rig Monitoring Platform. Collects hardware/software metrics via `psutil`, `pynvml`, and system interfaces, then POSTs them to the monitoring server every 60 seconds via cron.

## Requirements

- Linux (Ubuntu 20.04+, Debian 11+, or similar)
- Python 3.10+
- Network access to the monitoring server
- User with sudo permissions for installation

## Quick Start

### 1. Transfer Agent Files to the Rig

```bash
# check for sudo permissons
sudo su vastai
sudo ls
```
Suggested path for repo clone is home/current_user/workspace

```bash
# go home, create dir and clone repo, go to dir
cd ~
mkdir workspace
cd workspace
git clone https://github.com/dawmro/GPU-Rig-Monitoring-Platform.git
cd GPU-Rig-Monitoring-Platform
cd agent
```

### 2. Run the Installer
```
sudo apt update && sudo apt install python3.10-venv -y && sudo rm -rf /opt/monitoring-agent/venv && sudo chmod +x install.sh && sudo ./install.sh
```
If installing python3.10-venv not working, then use this fix for older python versions:
```
sudo apt update && sudo apt install python3.8-venv -y && sudo apt install -y python3-venv && sudo rm -rf /opt/monitoring-agent/venv && sudo chmod +x install.sh && sudo ./install.sh
```


> **Note:** Must be run with `bash`, not `sh`. The script uses bash-specific features like `set -o pipefail`.

The installer performs these operations:

| Step | What It Does |
|------|-------------|
| 1 | Creates `monitoring-agent` system user (no-login shell) |
| 2 | Creates directories: `/opt/monitoring-agent/`, `/etc/monitoring-agent/`, `/var/log/monitoring-agent/` |
| 3 | Creates Python virtualenv and installs dependencies (`psutil`, `py-cpuinfo`, `requests`, `pyyaml`, `nvidia-ml-py3`). Docker container monitoring uses the `docker` CLI via sudo — no Python SDK needed. |
| 4 | Copies `run.py` and creates config template at `/etc/monitoring-agent/config.yaml` |
| 5 | Configures sudoers (`/etc/sudoers.d/monitoring-agent`) for SMART disk queries, NVMe logs, journalctl, and docker (read-only, passwordless). Includes `Defaults:monitoring-agent !authenticate` (required for nologin shell users). |
| 6 | Creates cron job — runs every 60 seconds with `flock` to prevent overlaps |
| 7 | Schedules daily auto-update check (random time to avoid thundering herd) |

### 3. Configure

Edit the config file on the rig:

```bash
nano /etc/monitoring-agent/config.yaml
```

Set these values:

```yaml
rig_uuid: "auto"
rig_name: "gpu-server-01"
api_key: "your-api-key-from-dashboard"
server_endpoint: "https://monitor.example.com"
expected_gpu_count: 0
collection_timeout_s: 45
retry_attempts: 3
debug_mode: false
```

### 4. Test

```bash
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
```

Expected output:

```json
{"ts":"2026-06-01T17:35:04","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"transport","msg":"Ingest response: 200 {\"status\": \"new\"}"}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"main","msg":"Payload accepted: new"}
```

The cron job will start automatically within 1 minute.

## Config Reference

| Field | Default | Description |
|-------|---------|-------------|
| `rig_uuid` | `"auto"` | Auto-generates a permanent UUID on first run. |
| `rig_name` | `""` | Suggested initial name for this rig. Used ONLY once during first registration. Leave empty to use hostname. After creation, rename via the dashboard. |
| `api_key` | — | **Required.** API key from the dashboard. |
| `server_endpoint` | — | **Required.** Server HTTPS URL, no trailing slash. |
| `expected_gpu_count` | `0` | `0` = auto-detect. Set your GPU count to flag mismatches. |
| `collection_timeout_s` | `30` | Hard timeout (seconds) for metric collection + upload. Config example shows 45s as recommended value. |
| `retry_attempts` | `3` | Retries on transient failures (exponential backoff: 1s → 2s → 4s). |
| `debug_mode` | `false` | `true` = verbose logging. |

**To get an API key:** Log in to the monitoring dashboard → click **API Keys** → create a new key → copy it immediately (shown only once).

## File Layout

```
/opt/monitoring-agent/
├── run.py                   # Agent script (754 lines)
├── venv/                    # Python virtual environment
└── check_update.py          # Auto-update checker (separate script)

/etc/monitoring-agent/
└── config.yaml              # Agent config (mode 0600)

/var/log/monitoring-agent/
├── agent.log                # Structured JSON log (rotated at 10 MB × 3 backups)
├── payload.json             # Latest payload sent to server (overwritten each run)
├── cron.log                 # Cron output log
└── update.log               # Auto-update check log

/etc/cron.d/
├── monitoring-agent         # Agent schedule (every 60s)
└── monitoring-agent-update  # Auto-update check (daily at random time)
```

## What Gets Collected

| Metric | Source | Linux | Windows |
|--------|--------|-------|---------|
| CPU model, cores, load, temp, utilization, frequency (current/min/max) | psutil + cpuinfo + sysfs | ✅ | ✅ |
| Memory (total, used, free, cached, swap) | psutil | ✅ | ✅ |
| Motherboard (manufacturer, model, BIOS) | `/sys/class/dmi/` | ✅ | ✅ |
| Storage (partitions, capacity, usage, SMART/NVMe, temp, read/write bytes, IOPS) | psutil + `smartctl`/`nvme` | ✅ | ✅ |
| Network (interfaces, bytes, errors, speed) | psutil + sysfs | ✅ | ✅ |
| GPU (model, memory, util, temp, power, fan, PCIe link, core/mem clocks) | `pynvml` | ✅* | ✅* |
| GPU processes (per-process: name, type C/G/C+G, memory) | `pynvml` | ✅* | ✅* |
| Docker containers (name, image, status, container_id, uptime) | `docker` CLI (subprocess) | ✅† | ✅† |
| Top processes (top 20 by CPU and memory) | psutil two-pass | ✅ | ✅ |
| OS info (hostname, OS, kernel, uptime) | `platform` + psutil | ✅ | ✅ |
| NVIDIA driver version | `nvidia-smi` subprocess | ✅* | ✅* |
| System errors (with dedup, up to 1000 entries) | `journalctl` | ✅ | ✅ |
| Power consumption (CPU, GPU, total system) | RAPL sysfs + `pynvml` + calculation | ✅ | ✅ |

\* Requires NVIDIA GPU with drivers and `nvidia-ml-py3` installed.
† Requires Docker daemon running.

## Power Collection Details

The agent collects power consumption data from multiple sources and calculates total system power. All power values sent to the server are **AC (wall) watts** — PSU efficiency is already factored in.

### Collection Steps

1. **GPU Power** — Reads from `pynvml.nvmlDeviceGetPowerUsage(handle)` for each NVIDIA GPU. Returns power in milliwatts, converted to watts. Summed across all GPUs.

2. **CPU Power (RAPL)** — On Linux, reads energy counters from `/sys/class/powercap/intel-rapl:0/energy_uj` (Intel) or `/sys/class/powercap/amd-rapl:0/energy_uj` (AMD). Takes two readings 100ms apart, computes: `energy_joules / 0.1s = watts`. Validates range (0–1000W). Falls back to estimation if RAPL unavailable.

3. **CPU Power (Estimate)** — When RAPL is unavailable (VMs, old CPUs, containers), estimates using: `cpu_power = 10 + (8 × cores + 25) × (0.1 + 0.9 × utilization)`. Validated against Ryzen 3, 5, 7.

4. **Other Components** — Flat 40W estimate for RAM, disks, motherboard, and fans. This is conservative — actual draw is typically 30–45W for a GPU rig.

5. **Total Calculation** — `total_dc = gpu_power + cpu_power + 40`, then `total_ac = total_dc / 0.90` (PSU efficiency: 80 Plus Gold default).

### Payload Format

```json
{
  "power": {
    "cpu_power_w": 45.2,
    "cpu_power_source": "rapl",
    "gpu_power_w": 338.8,
    "other_power_w": 40,
    "total_power_w": 471.1
  }
}
```

`cpu_power_source` is either `"rapl"` (hardware measurement) or `"estimate"` (calculated from utilization).

### Server Storage

The server stores power data in two places:
- **LatestSnapshot** — latest values for Live Metrics display (`power_total_w`, `power_gpu_w`, `power_cpu_w`, `power_other_w`)
- **PowerReading** — historical timeseries, one row per minute (throttled), used for charts and cost estimation

## Agent Permissions

The `monitoring-agent` system user runs without root but needs elevated access for specific hardware queries:

| Command | Purpose | Risk |
|---------|---------|------|
| `/usr/sbin/smartctl` | Read disk SMART health data (SATA) | Read-only |
| `/usr/sbin/nvme` | Read NVMe drive health/temperature | Read-only |
| `/bin/journalctl` | Read system error logs | Read-only |

These are granted via `/etc/sudoers.d/monitoring-agent`:

```
Defaults:monitoring-agent !authenticate
monitoring-agent ALL=(root) NOPASSWD: /usr/sbin/smartctl, /usr/bin/smartctl, /bin/journalctl, /usr/bin/journalctl, /usr/sbin/nvme, /usr/bin/nvme
```

**Critical:** The `Defaults:monitoring-agent !authenticate` line is **required** for system users with `nologin` shell. Without it, PAM authentication fails. The `!authenticate` default tells sudo to skip PAM entirely; `NOPASSWD` alone is insufficient.

**GPU monitoring** does NOT require root — `pynvml` reads from the NVIDIA driver interface, accessible to all users.

## Auto-Update

The installer schedules a daily auto-update check at a random time. The update mechanism:

1. Checks GitHub for a newer agent version (same major version only)
2. Downloads the new `run.py`
3. Backs up the current version to `run.py.bak`
4. Validates the new version syntax
5. Atomically replaces the running script
6. New code is used on the next cron cycle (no restart needed)

Manual check:
```bash
sudo /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/check_update.py
```

Logs: `/var/log/monitoring-agent/update.log`

## Differences from Windows Agent

| Feature | Linux (`agent/`) | Windows (`agent_windows/`) |
|---------|-----------------|--------------------------|
| **Scheduling** | cron (every 60s) | Windows Task Scheduler (every 1 min) |
| **Lock file** | `flock` on `/var/lock/` | `msvcrt.locking` on `./logs/.agent.lock` |
| **Signal timeout** | `signal.SIGALRM` | Not needed (lock-based) |
| **Error collection** | `journalctl` | PowerShell `Get-WinEvent` |
| **System info** | `/sys/class/dmi/` | WMI (`Win32_BaseBoard`, `Win32_BIOS`) |
| **Disk SMART** | `sudo smartctl` / `nvme` | WMI `MSStorageDriver_FailurePredictStatus` |
| **Network speed** | `/sys/class/net/*/speed` | WMI `Win32_NetworkAdapter.Speed` |
| **Config path** | `/etc/monitoring-agent/config.yaml` | `./config.yaml` (alongside script) |
| **Log path** | `/var/log/monitoring-agent/` | `./logs/` (alongside script) |
| **Python deps** | `psutil py-cpuinfo requests pyyaml docker` | Same + `wmi` (+ `pynvml`, `docker` optional) |
| **Installation** | `install.sh` (bash) | `--install-task` flag or manual Task Scheduler |
| **Auto-update** | Separate `check_update.py` script via cron | Separate `check_update.py` script via Task Scheduler |
| **CLI flags** | None (run directly) | `--install-task`, `--remove-task`, `--help-task`, `--detect-server` |
| **Hidden window** | N/A | `pythonw.exe` |

## Troubleshooting

### Agent won't start

```bash
# Run with debug output
sudo -u monitoring-agent /opt/monitoring-agent/venv/bin/python /opt/monitoring-agent/run.py
# Check logs
tail -50 /var/log/monitoring-agent/agent.log
```

### API key errors (401)

- Regenerate the key on the dashboard
- Update `/etc/monitoring-agent/config.yaml` with the new key
- Ensure no extra spaces or quotes

### Connection errors

```bash
# Test connectivity to the server
curl -v https://monitor.example.com/api/v1/health/
```

### GPU metrics empty

```bash
# Install NVIDIA support
sudo /opt/monitoring-agent/venv/bin/pip install nvidia-ml-py3
```

Requires NVIDIA GPU with up-to-date drivers. If you see `FutureWarning: The pynvml package is deprecated`, install `nvidia-ml-py` instead — both provide the `pynvml` module.

### Docker metrics empty

The agent uses `sudo docker` CLI to collect container data. The `monitoring-agent` user needs passwordless sudo access to `/usr/bin/docker` and `/usr/local/bin/docker`. The install script configures this automatically via `/etc/sudoers.d/monitoring-agent`.

Verify Docker access:
```bash
sudo -u monitoring-agent sudo docker ps -a
```

If this fails, check:
1. Docker is installed and running: `sudo systemctl status docker`
2. Sudoers entry includes docker: `cat /etc/sudoers.d/monitoring-agent`
   - Should contain: `/usr/bin/docker, /usr/local/bin/docker`
   - If not, re-run the installer: `sudo bash /tmp/agent/install.sh`
3. Agent logs: `tail -50 /var/log/monitoring-agent/agent.log | grep docker`

**Note:** If the agent was installed before the Docker collection fix, the sudoers
file won't include docker. Re-run the installer to update it:
```bash
cd /tmp/agent && sudo bash install.sh
```
This will update the sudoers entry without affecting existing config or data.

### SMART/NVMe disk data unavailable

```bash
# Install disk tools
sudo apt install smartmontools nvme-cli
# Verify sudoers config
sudo -l -U monitoring-agent
```

### Cron job not running

```bash
# Check cron is running
systemctl status cron
# Check cron log
tail -50 /var/log/monitoring-agent/cron.log
# Verify cron file exists
cat /etc/cron.d/monitoring-agent
```

### Stale lock file (agent hangs/overlaps)

```bash
rm -f /var/lock/monitoring-agent.lock
```

### Permission denied on config

```bash
sudo chown monitoring-agent:monitoring-agent /etc/monitoring-agent/config.yaml
sudo chmod 600 /etc/monitoring-agent/config.yaml
```

## Command-Line Options

The Linux agent has no command-line flags. Run it directly:

```bash
python3 run.py                 # Collect and send metrics
```

Configuration is read from `/etc/monitoring-agent/config.yaml`. To test with debug logging, set `debug_mode: true` in the config file.
