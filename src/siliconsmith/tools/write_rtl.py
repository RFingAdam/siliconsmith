# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`write_rtl`: write the design source (and docs) of a project."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.paths import Category
from siliconsmith.result import ToolResult
from siliconsmith.tools._common import write_text_file

ACCEPTS: frozenset[Category] = frozenset({"rtl", "doc"})

DESCRIPTION = """\
Write a design source file or a doc, replacing the whole file. Always send the full new \
content: diffs and patches are not accepted. Allowed paths: hdl/**/*.py for Amaranth and MyHDL \
projects (the entry file is hdl/<name>.py); src/<name>.v for Verilog projects; docs/**/*.md \
(except the generated docs/verification.md). Tests go through write_test instead. Content must \
be UTF-8 text of at most 256 KiB. A .py file gets a syntax check (it is never run); the file is \
saved even when the check fails, and then ok is false with file:line:col and the message.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="write_rtl",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    def write_rtl(project: str, path: str, content: str) -> ToolResult:
        return write_text_file(
            ctx,
            tool="write_rtl",
            accepts=ACCEPTS,
            project_name=project,
            path=path,
            content=content,
        )
