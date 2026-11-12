# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The project model: `<root>/<name>/` with a `siliconsmith.toml` that holds name and front end."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.paths import safe_read, validate_project_name

FRONTENDS = ("amaranth", "myhdl", "verilog")
CONFIG_NAME = "siliconsmith.toml"
MAX_CONFIG_BYTES = 4096


@dataclass(frozen=True)
class Project:
    dir: Path
    name: str
    frontend: str


def render_config(name: str, frontend: str) -> str:
    """The text of `siliconsmith.toml`. Values are validated, so no escaping is needed."""
    validate_project_name(name)
    if frontend not in FRONTENDS:
        raise ToolError(f"unknown frontend {frontend!r}; choose one of: {', '.join(FRONTENDS)}")
    return f'name = "{name}"\nfrontend = "{frontend}"\n'


def list_projects(root: Path) -> list[str]:
    """Names of directories under `root` that contain a siliconsmith.toml, sorted."""
    found: list[str] = []
    try:
        entries = sorted(os.scandir(root), key=lambda e: e.name)
    except OSError:
        return found
    for entry in entries:
        if entry.is_dir(follow_symlinks=False) and (Path(entry.path) / CONFIG_NAME).is_file():
            found.append(entry.name)
    return found


def load_project(root: Path, name: str) -> Project:
    """Load project `name` from `root`; raise ToolError if it is invalid or does not exist."""
    validate_project_name(name)
    project_dir = root / name
    if not project_dir.is_dir() or project_dir.is_symlink():
        existing = list_projects(root)
        listing = ", ".join(existing) if existing else "none yet; call create_project first"
        raise ToolError(f"unknown project {name!r}. Existing projects: {listing}")
    raw = safe_read(project_dir, CONFIG_NAME, MAX_CONFIG_BYTES)
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ToolError(f"{name}/{CONFIG_NAME} is not valid TOML: {exc}") from exc
    if set(data) != {"name", "frontend"}:
        raise ToolError(f"{name}/{CONFIG_NAME} must contain exactly 'name' and 'frontend'")
    if data["name"] != name:
        raise ToolError(
            f"{name}/{CONFIG_NAME} says name = {data['name']!r}, which does not match the "
            "directory name"
        )
    frontend = data["frontend"]
    if frontend not in FRONTENDS:
        raise ToolError(
            f"{name}/{CONFIG_NAME} has frontend = {frontend!r}; expected one of "
            f"{', '.join(FRONTENDS)}"
        )
    return Project(dir=project_dir, name=name, frontend=frontend)


def validate_text(label: str, value: str, max_len: int) -> str:
    """Check a free-text field (author, title, description) that ends up in generated files.

    It must be one line of printable text without control characters and without `*/`, and
    at most `max_len` characters. Returns the stripped value.
    """
    if not isinstance(value, str):
        raise ToolError(f"{label} must be text")
    text = value.strip()
    if not text:
        raise ToolError(f"{label} must not be empty")
    if len(text) > max_len:
        raise ToolError(f"{label} is {len(text)} characters; the limit is {max_len}")
    if any(
        ord(ch) < 32 or ord(ch) == 127 or 0x80 <= ord(ch) < 0xA0 or ch in "\u2028\u2029"
        for ch in text
    ):
        raise ToolError(f"{label} must be a single line without control characters")
    if "*/" in text:
        raise ToolError(f"{label} must not contain '*/'")
    if re.search(r"coding[:=]", text):
        # PEP 263: such a comment on line 1 or 2 of a Python file changes how it is decoded.
        raise ToolError(f"{label} must not contain 'coding:' or 'coding='")
    return text
