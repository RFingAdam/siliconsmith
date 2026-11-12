# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`toolchain_info`: is Docker usable, is the toolchain image here, and what is in it?

This is the readiness check, so a missing Docker or image is reported as `ok=false` with hints,
not raised as a tool error.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from mcp.server import MCPServer

from siliconsmith.context import ServerContext
from siliconsmith.result import ToolResult
from siliconsmith.runners.base import Runner, WorkerOutcome
from siliconsmith.runners.docker import HostStatus, ToolchainError, pull_hint

INFO_TIMEOUT_S = 180.0
TOOL_ORDER = (
    "iverilog",
    "verilator",
    "yosys",
    "amaranth",
    "amaranth_yosys",
    "myhdl",
    "cocotb",
    "python",
    "pywellen",
)


def _host_status(runner: Runner) -> HostStatus | None:
    status_fn = getattr(runner, "host_status", None)
    return status_fn() if callable(status_fn) else None


def _run_info(runner: Runner) -> WorkerOutcome:
    # The info verb needs a project folder to mount; use an empty throwaway one.
    with tempfile.TemporaryDirectory(prefix="siliconsmith-info-") as scratch:
        return runner.run("info", {}, project_dir=Path(scratch), timeout_s=INFO_TIMEOUT_S)


def collect(ctx: ServerContext) -> ToolResult:
    runner = ctx.get_runner()
    details: dict[str, Any] = {
        "image": ctx.image,
        "docker_server_version": None,
        "rootless": None,
        "image_id": None,
        "repo_digest": None,
        "image_size_bytes": None,
        "versions": {},
        "pdk": None,
        "pdk_note": "The PDK arrives in milestone M1; nothing to report yet.",
    }
    status = _host_status(runner)
    if status is not None:
        details.update(
            docker_server_version=status.server_version,
            rootless=status.rootless,
            image_id=status.image_id,
            repo_digest=status.repo_digest,
            image_size_bytes=status.size_bytes,
        )
        if not status.docker_available:
            return ToolResult.from_lines(
                False,
                ["Docker is not usable.", status.error or "unknown error"],
                details=details,
                next_hints=[
                    "Install Docker Engine and start it, then check that `docker info` works "
                    "for your user.",
                    f"Then run `{pull_hint(ctx.image)}`.",
                ],
            )
        if not status.image_present:
            return ToolResult.from_lines(
                False,
                [f"The toolchain image {ctx.image} is not on this machine."],
                details=details,
                next_hints=[f"Run `{pull_hint(ctx.image)}`, then call toolchain_info again."],
            )
    try:
        outcome = _run_info(runner)
    except ToolchainError as exc:
        return ToolResult.from_lines(
            False,
            ["The toolchain container could not be started.", str(exc)],
            details=details,
            next_hints=[f"Run `{pull_hint(ctx.image)}` if the image is missing."],
        )
    result = outcome.result or {}
    if outcome.timed_out or outcome.exit_code != 0 or not result.get("ok"):
        message = (result.get("error") or {}).get("message") or outcome.stderr_tail.strip()
        return ToolResult.from_lines(
            False,
            ["The toolchain container started but its info check failed.", message[:400]],
            details={**details, "stderr_tail": outcome.stderr_tail},
            next_hints=["Pull the image again; if the problem stays, file an issue."],
        )
    versions: dict[str, Any] = dict(result.get("versions") or {})
    details["versions"] = versions
    lines = [f"Toolchain image {ctx.image}"]
    lines.append(f"  image id: {details['image_id']}")
    lines.append(f"  digest: {details['repo_digest'] or 'none (local build)'}")
    if details["docker_server_version"] is not None:
        mode = "rootless" if details["rootless"] else "rootful"
        lines.append(f"  docker {details['docker_server_version']}, {mode} mode")
    lines += [f"  {tool}: {versions.get(tool)}" for tool in TOOL_ORDER]
    lines.append("  pdk: not installed yet (arrives in M1)")
    return ToolResult.from_lines(True, lines, details=details)


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool()
    async def toolchain_info() -> ToolResult:
        """Check that Docker and the toolchain image work, and report the version of every tool.

        Call this first if anything else fails with a toolchain error.
        """
        return await asyncio.to_thread(collect, ctx)
