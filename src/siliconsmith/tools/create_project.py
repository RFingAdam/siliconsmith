# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`create_project`: make a Tiny Tapeout shaped project under the server root."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.project import FRONTENDS
from siliconsmith.result import Artifact, ToolResult
from siliconsmith.scaffold import create_project as scaffold_project

DESCRIPTION = """\
Create a new chip project folder <root>/<name>/ shaped like a Tiny Tapeout Verilog project, \
with a working starter design, a Tiny Tapeout wrapper (tt_um_<name>) and a cocotb test stub.

Arguments: name (lowercase letters, digits and underscores, starting with a letter, at most 31 \
characters; it becomes the Verilog module name), frontend ('amaranth', 'myhdl' or 'verilog'; \
optional when the server has a default), author (goes into file headers and info.yaml), \
description (one line), title (optional, defaults to the name).

The design you write is the 'core' module named <name>. Its ports use the Tiny Tapeout pin \
names: clk, rst_n (active low), ena, ui_in[7:0], uio_in[7:0] in; uo_out[7:0], uio_out[7:0], \
uio_oe[7:0] out. Amaranth and MyHDL projects keep their source in hdl/<name>.py; Verilog \
projects keep it in src/<name>.v. Every other tool takes the project name as its first argument.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="create_project",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    def create_project(
        name: str,
        author: str,
        description: str,
        frontend: str | None = None,
        title: str | None = None,
    ) -> ToolResult:
        chosen = frontend if frontend is not None else ctx.default_frontend
        if chosen is None:
            raise ToolError(f"frontend is required; choose one of: {', '.join(FRONTENDS)}")
        if chosen not in FRONTENDS:
            raise ToolError(f"unknown frontend {chosen!r}; choose one of: {', '.join(FRONTENDS)}")
        _project, files = scaffold_project(ctx.root, name, chosen, author, description, title)
        source = f"src/{name}.v" if chosen == "verilog" else f"hdl/{name}.py"
        lines = [
            f"Created project '{name}' ({chosen}) with {len(files)} files.",
            f"Top module for Tiny Tapeout: tt_um_{name} (src/project.v, generated).",
            f"Your design goes in {source}. Starter behaviour: uo_out = ui_in + uio_in.",
            "Tests go in test/ (starter: test/test.py).",
        ]
        return ToolResult.from_lines(
            True,
            lines,
            details={
                "project": name,
                "frontend": chosen,
                "top_module": f"tt_um_{name}",
                "source": source,
                "files": files,
            },
            artifacts=[Artifact(path=rel, kind="file") for rel in files],
            next_hints=[
                f"Call list_files with project='{name}' to see the layout.",
                f"Write the tests first with write_test (test/test.py), then the design in "
                f"{source} with write_rtl.",
            ],
        )
