# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Tool registry. One module per tool; each exposes `register(server, ctx)`."""

from __future__ import annotations

import importlib

from mcp.server import MCPServer

from siliconsmith.context import ServerContext

# Names of submodules of this package, for example "create_project". Later PRs add entries.
TOOL_MODULES: tuple[str, ...] = ("toolchain_info",)


def register_all(server: MCPServer, ctx: ServerContext) -> None:
    for name in TOOL_MODULES:
        module = importlib.import_module(f"{__name__}.{name}")
        module.register(server, ctx)
