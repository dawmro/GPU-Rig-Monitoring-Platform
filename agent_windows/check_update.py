#!/usr/bin/env python3
"""
GPU Rig Monitoring Agent — Auto-Update Checker (Windows)

Checks GitHub for a newer agent version and updates if available.
Designed to run once daily via Windows Task Scheduler at a random time.

Usage:
    python check_update.py

Exit codes:
    0 — No update needed, or update successful
    1 — Error (network, download, validation)
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

# ── Configuration ────────────────────────────────────────────────────────────

AGENT_DIR = Path(__file__).resolve().parent
RUN_PY = AGENT_DIR / "run.py"
BACKUP_PY = AGENT_DIR / "run.py.bak"
LOG_DIR = AGENT_DIR / "logs"
LOG_FILE = LOG_DIR / "update.log"

GITHUB_RAW_URL = (
    "https://raw.githubusercontent.com/dawmro/GPU-Rig-Monitoring-Platform"
    "/main/agent_windows/run.py"
)

# Only auto-update within same major version
MAX_MAJOR_VERSION = 1

# ── Logging ──────────────────────────────────────────────────────────────────

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
    root.addHandler(logging.StreamHandler(sys.stderr))

log = logging.getLogger(__name__)


# ── Version Parsing ──────────────────────────────────────────────────────────

def parse_version(version_str):
    """Parse version string like '1.2.0' or '1.2.0-win' into tuple (1, 2, 0)."""
    clean = re.split(r'[-_]', version_str.strip())[0]
    parts = clean.split(".")
    try:
        return tuple(int(p) for p in parts[:3])
    except (ValueError, IndexError):
        return None


def get_local_version():
    """Extract __version__ from local run.py."""
    if not RUN_PY.exists():
        return None, None
    content = RUN_PY.read_text()
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
    if match:
        ver_str = match.group(1)
        return ver_str, parse_version(ver_str)
    return None, None


def fetch_remote_version():
    """Fetch __version__ from GitHub raw run.py."""
    try:
        with urlopen(GITHUB_RAW_URL, timeout=30) as resp:
            content = resp.read().decode("utf-8")
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", content)
        if match:
            ver_str = match.group(1)
            return ver_str, parse_version(ver_str), content
        return None, None, None
    except (URLError, HTTPError, OSError) as e:
        log.warning("Failed to fetch remote version: %s", e)
        return None, None, None


# ── Validation ───────────────────────────────────────────────────────────────

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


# ── Update Logic ─────────────────────────────────────────────────────────────

def perform_update(new_content, new_version_str):
    """Download, validate, backup, and replace run.py."""
    # Write to temp file first
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", dir=str(AGENT_DIR), delete=False
        ) as tmp:
            tmp.write(new_content)
            tmp_path = Path(tmp.name)
    except OSError as e:
        log.error("Failed to write temp file: %s", e)
        return False

    try:
        # Validate syntax
        if not validate_python_file(tmp_path):
            log.error("Downloaded file has invalid syntax — aborting update")
            return False

        # Verify version in downloaded file
        downloaded_content = tmp_path.read_text()
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", downloaded_content)
        if not match:
            log.error("No version found in downloaded file — aborting")
            return False
        downloaded_ver = parse_version(match.group(1))
        if downloaded_ver is None or downloaded_ver != parse_version(new_version_str):
            log.error("Version mismatch in downloaded file — aborting")
            return False

        # Backup current run.py
        if RUN_PY.exists():
            shutil.copy2(RUN_PY, BACKUP_PY)
            log.info("Backed up current run.py to run.py.bak")

        # Replace (Windows: use os.replace for atomic operation)
        os.replace(str(tmp_path), str(RUN_PY))
        log.info("Successfully updated to version %s", new_version_str)
        return True

    except OSError as e:
        log.error("Failed to replace run.py: %s", e)
        return False
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    setup_logging()
    log.info("Starting update check")

    # Get local version
    local_ver_str, local_ver = get_local_version()
    if local_ver is None:
        log.error("Cannot determine local version — aborting")
        return 1
    log.info("Local version: %s (%s)", local_ver_str, local_ver)

    # Fetch remote version
    remote_ver_str, remote_ver, remote_content = fetch_remote_version()
    if remote_ver is None:
        log.warning("Cannot determine remote version — skipping update check")
        return 1
    log.info("Remote version: %s (%s)", remote_ver_str, remote_ver)

    # Compare versions
    if remote_ver <= local_ver:
        log.info("No update needed (local %s >= remote %s)", local_ver, remote_ver)
        return 0

    # Check major version boundary
    if remote_ver[0] > MAX_MAJOR_VERSION:
        log.info(
            "Major version bump detected (%s → %s) — manual update required",
            local_ver[0], remote_ver[0]
        )
        return 0

    # Update available
    log.info("Update available: %s → %s", local_ver_str, remote_ver_str)

    if perform_update(remote_content, remote_ver_str):
        log.info("Update complete. New version will be used on next scheduler cycle.")
        return 0
    else:
        log.error("Update failed — current version unchanged")
        return 1


if __name__ == "__main__":
    sys.exit(main())
