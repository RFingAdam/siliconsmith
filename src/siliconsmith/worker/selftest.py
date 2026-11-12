# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The `selftest` verb: reports how the sandbox looks from inside the container.

It exists so the integration tests can check the real `docker run` flags (read-only root,
read-only project mounts, writable `build/`, no network, non-root user).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

_MAX_SLEEP_S = 3600.0


def _can_create(directory: str) -> bool:
    probe = Path(directory) / f".ss-probe-{os.getpid()}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        return False
    os.close(fd)
    probe.unlink()
    return True


def _interfaces() -> list[str]:
    try:
        return sorted(os.listdir("/sys/class/net"))
    except OSError:
        return []


def _root_mounted_readonly() -> bool:
    """True if /proc/mounts lists `/` with the `ro` option (what `--read-only` does)."""
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        return False
    root = [line.split() for line in lines if len(line.split()) >= 4 and line.split()[1] == "/"]
    return bool(root) and "ro" in root[-1][3].split(",")


def run(request: dict[str, Any]) -> dict[str, Any]:
    sleep_s = float(request.get("sleep_s", 0) or 0)
    if sleep_s > 0:
        time.sleep(min(sleep_s, _MAX_SLEEP_S))
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "root_writable": _can_create("/"),
        "root_mounted_readonly": _root_mounted_readonly(),
        "vartmp_writable": _can_create("/var/tmp"),
        "src_writable": _can_create("/work/src"),
        "hdl_writable": _can_create("/work/hdl"),
        "test_writable": _can_create("/work/test"),
        "build_writable": _can_create("/work/build"),
        "mounts": {
            name: Path("/work", name).exists()
            for name in ("hdl", "src", "test", "spec.yaml", "info.yaml", "build")
        },
        "interfaces": _interfaces(),
        "home": os.environ.get("HOME"),
        "env": {
            key: os.environ.get(key)
            for key in (
                "XDG_CACHE_HOME",
                "CCACHE_DIR",
                "PYTHONDONTWRITEBYTECODE",
                "AMARANTH_USE_YOSYS",
                "PYTHONPATH",
            )
        },
    }
