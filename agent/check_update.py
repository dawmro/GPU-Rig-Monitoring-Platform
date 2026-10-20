#!/usr/bin/env python3
"""
GPU Rig Monitoring Agent - Auto-Update Checker

Checks server for a newer agent version and updates if available.
Designed to run once daily via cron at a random time.

Usage:
    python3 check_update.py

Exit codes:
    0 - No update needed, or update successful
    1 - Error (network, download, validation)
"""

import os
import sys
import re
import shutil
import logging
import logging.handlers
import tempfile
import subprocess
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

__version__ = '1.0.0'

# -- Configuration ------------------------------------------------------------

AGENT_DIR = Path(__file__).resolve().parent
RUN_PY = AGENT_DIR / "run.py"
BACKUP_PY = AGENT_DIR / "run.py.bak"
CHECK_UPDATE_PY = AGENT_DIR / "check_update.py"
CHECK_UPDATE_BAK = AGENT_DIR / "check_update.py.bak"
LOG_DIR = Path("/opt/monitoring-agent/var/log")
LOG_FILE = LOG_DIR / "update.log"

# Only auto-update within same major version (1.x -> 1.y, not 1.x -> 2.x)
MAX_MAJOR_VERSION = 1

# -- Logging ------------------------------------------------------------------

def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # Also log to stderr for cron capture
    root.addHandler(logging.StreamHandler(sys.stderr))

log = logging.getLogger(__name__)


# -- Version Parsing ----------------------------------------------------------

def parse_version(version_str):
    """Parse version string like '1.2.0' or '1.2.0-win' into tuple (1, 2, 0)."""
    # Strip platform suffix (-win, -linux, etc.)
    clean = re.split(r'[-_]', version_str.strip())[0]
    parts = clean.split(".")
    try:
        return tuple(int(p) for p in parts[:3])
    except (ValueError, IndexError):
        return None


def get_local_version(filepath):
    """Extract __version__ from a Python file."""
    if not filepath.exists():
        return None, None
    content = filepath.read_text(encoding="utf-8")
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if match:
        ver_str = match.group(1)
        return ver_str, parse_version(ver_str)
    return None, None


def fetch_remote_version(url, timeout=30):
    """Fetch __version__ from a remote Python file URL."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
        if match:
            ver_str = match.group(1)
            return ver_str, parse_version(ver_str), content
        return None, None, None
    except (URLError, HTTPError, OSError) as e:
        log.warning("Failed to fetch remote version from %s: %s", url, e)
        return None, None, None


# -- Validation ---------------------------------------------------------------

def validate_python_file(path):
    """Check that a Python file has valid syntax."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            log.error("Syntax validation failed: %s", result.stderr.strip())
            return False
        return True
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("Syntax validation error: %s", e)
        return False


def check_disk_space(path, min_bytes=10 * 1024 * 1024):
    """Check that we have enough disk space."""
    try:
        stat = os.statvfs(path)
        free = stat.f_bavail * stat.f_frsize
        return free >= min_bytes
    except OSError:
        return True  # Can't check, assume OK


# -- Update Logic -------------------------------------------------------------

def perform_update(filepath, backup_path, new_content, new_version_str, file_type):
    """Download, validate, backup, and replace a Python file."""
    # Check disk space
    if not check_disk_space(filepath.parent):
        log.error("Insufficient disk space for %s update", file_type)
        return False

    # Write to temp file first (same directory as target for atomic replace on Windows)
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=str(filepath.parent), delete=False,
            encoding="utf-8"
        ) as tmp:
            tmp.write(new_content)
            tmp_path = Path(tmp.name)
    except OSError as e:
        log.error("Failed to write temp file for %s: %s", file_type, e)
        return False

    try:
        # Validate syntax
        if not validate_python_file(tmp_path):
            log.error("Downloaded %s has invalid syntax - aborting update", file_type)
            return False

        # Verify version in downloaded file matches expected
        downloaded_ver_str, downloaded_ver = get_local_version_from_content(
            tmp_path.read_text(encoding="utf-8")
        )
        if downloaded_ver is None or downloaded_ver != parse_version(new_version_str):
            log.error("Version mismatch in downloaded %s - aborting update", file_type)
            return False

        # Backup current file
        if filepath.exists():
            shutil.copy2(filepath, backup_path)
            log.info("Backed up current %s to %s", file_type, backup_path.name)

        # Atomic replace
        tmp_path.replace(filepath)
        # Set permissions: owner read/write/execute, group/other execute
        os.chmod(str(filepath), 0o755)
        os.chmod(str(backup_path), 0o755)
        log.info("Successfully updated %s to version %s", file_type, new_version_str)
        return True

    except OSError as e:
        log.error("Failed to replace %s: %s", file_type, e)
        return False
    finally:
        # Clean up temp file if it still exists
        if tmp_path.exists():
            tmp_path.unlink()


def get_local_version_from_content(content):
    """Extract version from file content string."""
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if match:
        ver_str = match.group(1)
        return ver_str, parse_version(ver_str)
    return None, None


def update_single_file(filepath, backup_path, server_url, file_type, config):
    """
    Update a single file (run.py or check_update.py) from server.
    Returns (success: bool, updated: bool)
    """
    # Get local version
    local_ver_str, local_ver = get_local_version(filepath)
    if local_ver is None:
        log.error("Cannot determine local %s version - aborting", file_type)
        return False, False
    log.info("Local %s version: %s (%s)", file_type, local_ver_str, local_ver)

    # Try server
    server_ver_str, server_ver, server_content = fetch_remote_version(server_url)
    if server_ver is None:
        log.warning("Cannot determine server %s version", file_type)
        return False, False

    log.info("Server %s version: %s (%s)", file_type, server_ver_str, server_ver)

    # Compare versions
    if server_ver <= local_ver:
        log.info("No %s update needed (local %s >= server %s)", file_type, local_ver, server_ver)
        return True, False

    # Check major version boundary
    if server_ver[0] > MAX_MAJOR_VERSION:
        log.info(
            "Major version bump detected (%s -> %s) - manual update required for %s",
            local_ver[0], server_ver[0], file_type
        )
        return True, False

    # Update available
    log.info("%s update available: %s -> %s", file_type.capitalize(), local_ver_str, server_ver_str)

    if perform_update(filepath, backup_path, server_content, server_ver_str, file_type):
        log.info("%s update complete. New version will be used on next cron cycle.", file_type.capitalize())
        return True, True
    else:
        log.error("%s update failed - current version unchanged", file_type.capitalize())
        return False, False


# -- Main ---------------------------------------------------------------------

def load_config():
    """Load agent configuration from /etc/monitoring-agent/config.yaml"""
    import yaml
    config_path = Path("/etc/monitoring-agent/config.yaml")
    if not config_path.exists():
        log.error("Config file not found: %s", config_path)
        return {}
    try:
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        log.error("Failed to load config: %s", e)
        return {}


def main():
    setup_logging()
    log.info("Starting update check")

    # Load config
    config = load_config()
    server_endpoint = config.get('server_endpoint', '').rstrip('/')
    if not server_endpoint:
        log.error("server_endpoint not configured - aborting")
        return 1

    # Determine platform-specific paths
    is_windows = sys.platform.startswith('win')
    if is_windows:
        run_server_url = f"{server_endpoint}/agent_windows/run.py"
        check_update_server_url = f"{server_endpoint}/agent_windows/check_update.py"
    else:
        run_server_url = f"{server_endpoint}/agent/run.py"
        check_update_server_url = f"{server_endpoint}/agent/check_update.py"

    # Update run.py
    run_ok, run_updated = update_single_file(
        RUN_PY, BACKUP_PY, run_server_url, "run.py", config
    )
    if not run_ok:
        return 1

    # Update check_update.py (self-update)
    cu_ok, cu_updated = update_single_file(
        CHECK_UPDATE_PY, CHECK_UPDATE_BAK, check_update_server_url, "check_update.py", config
    )
    if not cu_ok:
        return 1

    if run_updated or cu_updated:
        log.info("Update check complete: changes applied")
    else:
        log.info("Update check complete: no updates needed")

    return 0


if __name__ == "__main__":
    sys.exit(main())