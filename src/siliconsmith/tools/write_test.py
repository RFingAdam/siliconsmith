# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`write_test`: write a cocotb test file."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.paths import Category
from siliconsmith.result import ToolResult
from siliconsmith.tools._common import write_text_file

ACCEPTS: frozenset[Category] = frozenset({"test"})

DESCRIPTION = """\
Write a cocotb 2.x test file under test/ (test/**/*.py, but not conftest.py), replacing the \
whole file. Always send the full new content. The starter test is test/test.py. cocotb 2.x \
idioms: Clock(dut.clk, 10, unit="ns"), int(dut.x.value), cocotb.start_soon. Give each test a \
docstring line such as 'Covers: R1' naming the requirement ids from spec.yaml. Content must be \
UTF-8 text of at most 256 KiB. The file gets a syntax check (it is never run here); it is saved \
even when the check fails, and then ok is false with file:line:col and the message.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="write_test",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    def write_test(project: str, path: str, content: str) -> ToolResult:
        return write_text_file(
            ctx,
            tool="write_test",
            accepts=ACCEPTS,
            project_name=project,
            path=path,
            content=content,
        )
