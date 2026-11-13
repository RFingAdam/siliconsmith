# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`list_files`: show the files of a project."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.paths import walk_files
from siliconsmith.result import ToolResult
from siliconsmith.tools._common import get_project

MAX_ENTRIES = 500
HIDDEN = frozenset({"build", "sim_build", "__pycache__"})

DESCRIPTION = """\
List the files in a project (paths relative to the project folder, with sizes in bytes). \
Symlinks and dotfiles are never listed. Build output (build/, sim_build/) is hidden unless \
include_build is true. At most 500 files are returned.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="list_files",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def list_files(project: str, include_build: bool = False) -> ToolResult:
        proj = get_project(ctx, project)
        entries = walk_files(
            proj.dir,
            skip_dirs=frozenset() if include_build else HIDDEN,
            limit=MAX_ENTRIES,
        )
        shown = entries[:MAX_ENTRIES]
        truncated = len(entries) > MAX_ENTRIES
        count = f"more than {MAX_ENTRIES}" if truncated else str(len(entries))
        lines = [f"{project}: {count} files ({proj.frontend})"]
        lines += [f"{path}  {size} B" for path, size in shown]
        return ToolResult.from_lines(
            True,
            lines,
            details={
                "files": [{"path": path, "size": size} for path, size in shown],
                "total": None if truncated else len(entries),
                "truncated": truncated,
            },
        )
