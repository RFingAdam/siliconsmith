# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Lay out a new project from the pinned Tiny Tapeout template snapshot (PLAN.md section 5.2).

The snapshot lives in `templates/tt/` (see `templates/tt/UPSTREAM` for its source commit and the
patches applied here). Our own starter files live in `templates/stubs/`. Nothing here depends on
the root path or the clock, so the same inputs give byte-identical files in any root.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.paths import atomic_write, project_lock, validate_project_name
from siliconsmith.project import (
    CONFIG_NAME,
    FRONTENDS,
    Project,
    render_config,
    validate_text,
)
from siliconsmith.wrapper import TT_PORTS, generate_wrapper

TEMPLATE_DIR = Path(__file__).parent / "templates"
TT_DIR = TEMPLATE_DIR / "tt"
STUB_DIR = TEMPLATE_DIR / "stubs"

# Template files copied as they are (the patched ones are handled in `build_files`).
VERBATIM = ("src/config.json", "test/requirements.txt", "docs/info.md")

LANGUAGE = {"amaranth": "Amaranth", "myhdl": "MyHDL", "verilog": "Verilog"}

# The ports each starter core declares. The Amaranth and Verilog starters declare all eight Tiny
# Tapeout ports. The MyHDL starter leaves out clk, rst_n and ena, because MyHDL rejects ports
# that nothing reads (see the starter's docstring).
_DIRECTIONS = {port: direction for port, (direction, _) in TT_PORTS.items()}
STUB_PORTS: dict[str, dict[str, str]] = {
    "amaranth": dict(_DIRECTIONS),
    "verilog": dict(_DIRECTIONS),
    "myhdl": {p: d for p, d in _DIRECTIONS.items() if p not in ("clk", "rst_n", "ena")},
}


def _tt_text(rel: str) -> str:
    return (TT_DIR / rel).read_text(encoding="utf-8")


def _stub(rel: str, name: str) -> str:
    return (STUB_DIR / rel).read_text(encoding="utf-8").replace("{name}", name)


def _replace_once(text: str, old: str, new: str, what: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"template snapshot changed: expected one {what} to patch")
    return text.replace(old, new)


def _info_yaml(name: str, frontend: str, author: str, description: str, title: str) -> str:
    info: dict[str, Any] = yaml.safe_load(_tt_text("info.yaml"))
    project = info["project"]
    project["title"] = title
    project["author"] = author
    project["description"] = description
    project["language"] = LANGUAGE[frontend]
    project["top_module"] = f"tt_um_{name}"
    project["source_files"] = ["project.v", f"{name}.v"]
    project["tiles"] = "1x1"
    header = "# Tiny Tapeout project information (written by Siliconsmith).\n"
    return header + yaml.safe_dump(info, sort_keys=False, allow_unicode=True)


def _with_header(text: str, author: str, comment: str, *, tiny_tapeout: bool = False) -> str:
    """Prefix the SPDX header; `tiny_tapeout` keeps the upstream line for files adapted from TT."""
    upstream = f"{comment} SPDX-FileCopyrightText: 2024 Tiny Tapeout\n" if tiny_tapeout else ""
    return (
        f"{comment} SPDX-FileCopyrightText: {author}\n"
        f"{upstream}"
        # REUSE-IgnoreStart
        f"{comment} SPDX-License-Identifier: Apache-2.0\n\n{text}"
        # REUSE-IgnoreEnd
    )


def build_files(
    name: str, frontend: str, author: str, description: str, title: str
) -> dict[str, str]:
    """Return every file of a new project as `{relative path: text}` (reads templates only)."""
    files: dict[str, str] = {
        CONFIG_NAME: render_config(name, frontend),
        "info.yaml": _info_yaml(name, frontend, author, description, title),
    }
    if frontend == "verilog":
        files[f"src/{name}.v"] = _with_header(
            _stub("verilog.v.tmpl", name), author, "//", tiny_tapeout=True
        )
    else:
        files[f"hdl/{name}.py"] = _with_header(_stub(f"{frontend}.py.tmpl", name), author, "#")
    files["src/project.v"] = generate_wrapper(name, STUB_PORTS[frontend], author)
    for rel in VERBATIM:
        files[rel] = _tt_text(rel)
    files["test/tb.v"] = _replace_once(
        _tt_text("test/tb.v"),
        "  tt_um_example user_project (",
        f"  tt_um_{name} user_project (",
        "tb.v instance",
    )
    files["test/Makefile"] = _replace_once(
        _tt_text("test/Makefile"),
        "PROJECT_SOURCES = project.v\n",
        f"PROJECT_SOURCES = project.v {name}.v\n",
        "Makefile PROJECT_SOURCES line",
    )
    files["test/test.py"] = _with_header(
        _stub("test.py.tmpl", name), author, "#", tiny_tapeout=True
    )
    return files


def create_project(
    root: Path,
    name: str,
    frontend: str,
    author: str,
    description: str,
    title: str | None = None,
) -> tuple[Project, list[str]]:
    """Create `<root>/<name>/`; return the project and the sorted relative paths written."""
    validate_project_name(name)
    if frontend not in FRONTENDS:
        raise ToolError(f"unknown frontend {frontend!r}; choose one of: {', '.join(FRONTENDS)}")
    author = validate_text("author", author, 100)
    description = validate_text("description", description, 200)
    title = validate_text("title", title if title is not None else name, 100)
    project_dir = root / name
    if os.path.lexists(project_dir):
        raise ToolError(
            f"project {name!r} already exists under the root; pick another name or use the "
            "existing project"
        )
    files = build_files(name, frontend, author, description, title)
    try:
        os.mkdir(project_dir)
    except FileExistsError:
        raise ToolError(f"project {name!r} already exists under the root") from None
    except OSError as exc:
        raise ToolError(f"cannot create project directory: {exc.strerror}") from exc
    try:
        with project_lock(project_dir):
            for rel, text in files.items():
                atomic_write(project_dir, rel, text.encode("utf-8"))
            (project_dir / "build").mkdir()
    except BaseException:
        shutil.rmtree(project_dir, ignore_errors=True)
        raise
    return Project(dir=project_dir, name=name, frontend=frontend), sorted(files)
