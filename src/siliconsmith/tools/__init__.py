# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tool registry. One module per tool; each exposes `register(server, ctx)`."""

from __future__ import annotations

import importlib

from mcp.server import MCPServer

from siliconsmith.context import ServerContext

# Names of submodules of this package, for example "create_project". Later PRs add entries.
TOOL_MODULES: tuple[str, ...] = (
    "toolchain_info",
    "create_project",
    "list_files",
    "read_file",
    "write_rtl",
    "write_test",
    "write_spec",
    "emit_verilog",
    "run_tests",
    "inspect_waves",
    "synthesize",
    "status",
    "explain",
)


def register_all(server: MCPServer, ctx: ServerContext) -> None:
    for name in TOOL_MODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        module.register(server, ctx)
