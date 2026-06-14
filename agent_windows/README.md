# GPU Rig Monitoring Agent — Windows

**Version:** 1.6.4-win | **Schema:** 1.6

Windows-compatible agent for the GPU Rig Monitoring Platform. Collects hardware/software metrics and sends them to the monitoring server via HTTPS.

## Requirements

- Windows 10 or later
- Python 3.10+
- Network access to the monitoring server

## Quick Start

### 0. Create a Virtual Environment (Recommended)

Using a virtual environment keeps the agent's dependencies isolated from your system Python:

```powershell
cd agent_windows
python -m venv venv
.\venv\Scripts\Activate.ps1
```

> **Note:** If you get an execution policy error, run `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` first, or use `.\venv\Scripts\activate.bat` in Command Prompt instead.

After activating the venv, your prompt should show `(venv)`. All `pip install` and `python run.py` commands below should be run inside the activated venv.

To deactivate when done:
```powershell
deactivate
```

### 1. Install Python Dependencies

Install core dependencies inside the activated venv:

```powershell
pip install psutil py-cpuinfo requests pyyaml wmi
```

Optional dependencies:

```powershell
pip install pynvml         # For NVIDIA GPU monitoring (Requires NVIDIA GPU with drivers)
```

**Note:** Docker container monitoring uses the `docker` CLI directly (via subprocess) and does NOT require the `docker` Python SDK. Docker Desktop must be installed and running.

### 1b. Freeze Dependencies (Optional)

To record exact versions for reproducibility:

```powershell
pip freeze > requirements.txt
```

To install from a requirements file on another machine:

```powershell
pip install -r requirements.txt
```

### 2. Configure

Copy the example config and edit it:

```powershell
copy config.yaml.example config.yaml
notepad config.yaml
```

Set these values:

```yaml
rig_uuid: "auto"
api_key: "your-api-key-from-dashboard"
server_endpoint: "http://192.168.253.1"
expected_gpu_count: 0
retry_attempts: 3
debug_mode: true
```

> **Important:** The `server_endpoint` must include the scheme — `http://` or `https://`. For example, if your server is at `192.168.253.1`, use `http://192.168.253.1` (not just `192.168.253.1`).

**To get an API key:** Log in to the monitoring dashboard → click **API Keys** → create a new key → copy it immediately (shown only once).

### 3. Test

```powershell
python run.py
```

You should see JSON log output. On success:

```json
{"ts":"2026-06-01T17:35:04","level":"INFO","module":"main","msg":"Starting collection for rig a1b2c3d4-..."}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"transport","msg":"Ingest response: 200 {\"status\": \"new\"}"}
{"ts":"2026-06-01T17:35:06","level":"INFO","module":"main","msg":"Payload accepted: new"}
```

### 4. Set Up Automatic Scheduling

> **Note:** The agent is designed to run every 60 seconds (matching the Linux cron schedule). Windows Task Scheduler's minimum interval is 1 minute. The `--install-task` flag configures 1-minute intervals, which is the closest practical equivalent. The built-in file lock prevents overlapping runs.

**Option A — Automatic (run CMD as Administrator):**

```cmd
call venv/Scripts/activate
python run.py --install-task
```

This creates a Windows Task Scheduler entry that runs the agent every 1 minute using `pythonw.exe` (hidden window).

Verify it was created:

```powershell
schtasks /query /tn GPURigMonitorAgent
```

**Option B — Manual via Task Scheduler GUI:**

```powershell
python run.py --help-task
```

Then follow the printed instructions.

**Option C — Manual via command line (run as Administrator):**

```powershell
schtasks /create /tn "GPURigMonitorAgent" /tr "python C:\path\to\agent_windows\run.py" /sc minute /mo 1 /f
```

### 5. Remove Scheduled Task

```powershell
python run.py --remove-task
```

Or:

```powershell
schtasks /delete /tn GPURigMonitorAgent /f
```

## Config Reference

| Field | Default | Description |
|-------|---------|-------------|
| `rig_uuid` | `"auto"` | Auto-generates a permanent UUID on first run. |
| `api_key` | — | **Required.** API key from the dashboard. |
| `server_endpoint` | — | **Required.** Server URL, must include `http://` or `https://`. |
| `expected_gpu_count` | `0` | `0` = auto-detect. Set your GPU count to flag mismatches. |
| `retry_attempts` | `3` | Retries on transient failures (exponential backoff: 1s → 2s → 4s). |
| `debug_mode` | `false` | `true` = verbose logging. |

**To get an API key:** Log in to the monitoring dashboard → click **API Keys** → create a new key → copy it immediately (shown only once).

## File Layout

```
agent_windows/
├── run.py                   # Agent script (1079 lines)
├── check_update.py          # Auto-update checker (separate script)
├── config.yaml.example      # Config template
├── config.yaml              # Your config (create from example)
└── logs/                    # Created automatically
    ├── agent.log            # Structured JSON log (rotated at 10 MB × 3 backups)
    └── payload.json         # Latest full JSON payload sent to server (overwritten each run)
```

## What Gets Collected

| Metric | Source | Windows | Linux |
|--------|--------|---------|-------|
| CPU model, cores, load, temp, utilization | psutil + cpuinfo + WMI | ✅ | ✅ |
| Memory (total, used, free, cached, swap) | psutil | ✅ | ✅ |
| Motherboard (manufacturer, model, BIOS) | WMI (`Win32_BaseBoard`, `Win32_BIOS`) | ✅ | ✅ |
| Storage (partitions, capacity, usage, SMART) | psutil + WMI | ✅ | ✅ |
| Network (interfaces, bytes, errors, speed) | psutil + WMI | ✅ | ✅ |
| GPU (model, memory, util, temp, power, fan, PCIe link, core/mem clocks) | `pynvml` | ✅* | ✅* |
| GPU processes (per-process: name, type C/G/C+G, memory) | `nvidia-smi` subprocess | ✅* | ✅* |
| Docker containers (name, image, status, container_id, uptime, restarts, cpu%, memory, mem_limit) | `docker` CLI (subprocess) | ✅† | ✅† |
| OS info (hostname, OS, kernel, uptime) | `platform` + psutil | ✅ | ✅ |
| NVIDIA driver version | `nvidia-smi` subprocess | ✅* | ✅* |
| System errors (last 5 min) | PowerShell `Get-WinEvent` | ✅ | ✅ |

\* Requires NVIDIA GPU with drivers and `nvidia-ml-py3` installed.
† Requires Docker Desktop running.

## Permissions

The agent needs **administrator access** for some hardware queries (disk SMART via WMI, Windows Event Log). When using Task Scheduler, enable **"Run with highest privileges"** — this is configured automatically by `--install-task`.

GPU monitoring does NOT require admin — `pynvml` reads from the NVIDIA driver interface, accessible to all users.

## Auto-Update

Auto-update is handled by a separate `check_update.py` script (not built into `run.py`). The update mechanism:

1. Checks GitHub for a newer agent version (same major version only)
2. Downloads the new `run.py`
3. Backs up the current version to `run.py.bak`
4. Validates the new version syntax
5. Atomically replaces the running script
6. New code is used on the next scheduler cycle (no restart needed)

Manual check:
```powershell
python check_update.py
```

Logs: `logs/update.log`

## Differences from Linux Agent

| Feature | Linux (`agent/`) | Windows (`agent_windows/`) |
|---------|-----------------|--------------------------|
| **Scheduling** | cron (every 60s) | Windows Task Scheduler (every 1 min) |
| **Lock file** | `flock` on `/var/lock/` | `msvcrt.locking` on `./logs/.agent.lock` |
| **Signal timeout** | `signal.SIGALRM` (default 30s) | Not needed (lock-based) |
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

## Command-Line Options

```
python run.py                    # Collect and send metrics
python run.py --detect-server    # Auto-detect server IP on local network
python run.py --install-task     # Create Windows Task Scheduler entry (Admin required)
python run.py --remove-task      # Remove Windows Task Scheduler entry (Admin required)
python run.py --help-task        # Print Task Scheduler setup instructions
```

## Troubleshooting

### Agent won't start

```powershell
# Run with debug output
python run.py
# Check the logs directory
type logs\agent.log
```

### API key errors (401)

- Regenerate the key on the dashboard
- Update `config.yaml` with the new key
- Ensure no extra spaces or quotes

### Connection errors

```powershell
# Test connectivity to the server
curl https://monitor.example.com/api/v1/health/
```

### Connection errors / WinError 10061 (Connection refused)

The server is not reachable at the configured `server_endpoint`. Common causes:

**Wrong IP address:** The VMware NAT gateway (`192.168.253.1`) is not the VM's IP. The VM gets a DHCP address in the `192.168.253.x` range (e.g., `192.168.253.131`).

Use the auto-detect tool:

```powershell
python run.py --detect-server
```

Or find the VM's IP manually from the VM:

```bash
ip addr show | grep 'inet ' | awk '{print $2}'
```

Then update `config.yaml`:

```yaml
server_endpoint: "http://192.168.253.131"
```

**Server not running:** Check the server from the VM:

```bash
curl -s http://localhost/api/v1/health/ | python3 -m json.tool
```

**Firewall on VM:** Ensure port 80 is open:

```bash
sudo iptables -L INPUT -n | grep 80
```

### GPU metrics empty

Install NVIDIA support:

```powershell
pip install pynvml
```

Requires NVIDIA GPU with up-to-date drivers.

> **Note:** You may see a `FutureWarning: The pynvml package is deprecated. Please install nvidia-ml-py instead.` This is harmless — both packages provide the same `pynvml` module. The warning comes from the `pynvml` package itself.

### SMART disk data unavailable

Ensure Task Scheduler is configured with **"Run with highest privileges"** — SMART queries require administrator access.

### Docker metrics empty

**Linux:** The agent uses `sudo docker` to collect container data. Ensure the `monitoring-agent` user has passwordless sudo access to `/usr/bin/docker` and `/usr/local/bin/docker`. The install script configures this automatically. Verify with:
```bash
sudo -u monitoring-agent sudo docker ps -a
```

**Windows:** The agent uses `docker` CLI. Ensure Docker Desktop is running and the user has permissions to run `docker ps`.

If containers still don't appear, check the agent logs:
```bash
# Linux
tail -50 /var/log/monitoring-agent/agent.log | grep docker

# Windows
type logs\agent.log | findstr docker
```

### High CPU during collection

The agent collects CPU metrics with a 1-second `psutil.cpu_percent(interval=1)` call. This is normal and only happens once per run.
