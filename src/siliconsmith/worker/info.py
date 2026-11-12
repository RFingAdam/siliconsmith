# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The `info` verb: versions of every tool in the image."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import re
import subprocess
from pathlib import Path
from typing import Any

PDK_MANIFEST = Path("/opt/pdk/manifest.json")
_TOOL_COMMANDS: dict[str, list[str]] = {
    "iverilog": ["iverilog", "-V"],
    "verilator": ["verilator", "--version"],
    "yosys": ["yosys", "-V"],
}
_DISTRIBUTIONS = {
    "amaranth": "amaranth",
    "amaranth_yosys": "amaranth-yosys",
    "myhdl": "myhdl",
    "cocotb": "cocotb",
    "pywellen": "pywellen",
}


def _first_line(command: list[str]) -> str | None:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (proc.stdout or proc.stderr).strip().splitlines()
    return lines[0].strip() if proc.returncode == 0 and lines else None


def _short(tool: str, line: str | None) -> str | None:
    if line is None:
        return None
    patterns = {
        "iverilog": r"Icarus Verilog version (\S+)",
        "verilator": r"Verilator (\S+)",
        "yosys": r"Yosys (\S+)",
    }
    match = re.search(patterns[tool], line)
    return match.group(1) if match else line


def _dist_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def pdk_info(manifest: Path = PDK_MANIFEST) -> dict[str, Any] | None:
    """What the image says it holds (name, open_pdks hash, libraries, corner), or None.

    None means the manifest is missing or unreadable, which is what an image from before M1
    looks like.
    """
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return {
            "name": str(data["name"]),
            "open_pdks": str(data["open_pdks"]),
            "libraries": [str(lib) for lib in data["libraries"]],
            "corner": str(data["corner"]),
        }
    except (OSError, ValueError, KeyError, TypeError):
        return None


def collect() -> dict[str, Any]:
    versions: dict[str, Any] = {
        tool: _short(tool, _first_line(command)) for tool, command in _TOOL_COMMANDS.items()
    }
    for key, dist in _DISTRIBUTIONS.items():
        versions[key] = _dist_version(dist)
    versions["python"] = platform.python_version()
    return {"versions": versions, "pdk": pdk_info()}
