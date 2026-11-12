# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`toolchain_info`: is Docker usable, is the toolchain image here, and what is in it?

This is the readiness check, so a missing Docker or image is reported as `ok=false` with hints,
not raised as a tool error.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, ValidationError

from siliconsmith.context import ServerContext
from siliconsmith.result import ToolResult
from siliconsmith.runners.base import Runner, WorkerOutcome
from siliconsmith.runners.docker import HostStatus, ToolchainError, pull_hint
from siliconsmith.runners.worker_cache import cache_home

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


class _InfoError(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    kind: str = ""
    message: str = ""


class _InfoPdk(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    name: str
    open_pdks: str
    libraries: list[str]
    corner: str


class _InfoResult(BaseModel):
    """The shape of the info verb's result. It comes from the container, so it is checked."""

    model_config = ConfigDict(strict=True, extra="ignore")
    ok: bool
    versions: dict[str, str | None] = {}
    pdk: _InfoPdk | None = None
    error: _InfoError | None = None


def _host_status(runner: Runner) -> HostStatus | None:
    status_fn = getattr(runner, "host_status", None)
    return status_fn() if callable(status_fn) else None


def _run_info(runner: Runner) -> WorkerOutcome:
    # The info verb needs a project folder to mount. A fixed folder in the user's cache keeps the
    # compiled Yosys cache (about 90 MB) between calls, instead of copying it into /tmp each time.
    scratch = cache_home() / "siliconsmith" / "info-project"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        return runner.run("info", {}, project_dir=scratch, timeout_s=INFO_TIMEOUT_S)
    finally:
        shutil.rmtree(scratch / "build" / ".ss", ignore_errors=True)  # keep the Yosys cache


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
    try:
        info = _InfoResult.model_validate(outcome.result or {})
    except ValidationError:
        info = None
    if outcome.timed_out or outcome.exit_code != 0 or info is None or not info.ok:
        message = (
            info.error.message
            if info is not None and info.error is not None
            else outcome.stderr_tail
        ).strip()
        if info is None and outcome.result is not None:
            message = "the info check returned an unreadable result"
        return ToolResult.from_lines(
            False,
            ["The toolchain container started but its info check failed.", message[:400]],
            details={**details, "stderr_tail": outcome.stderr_tail},
            next_hints=["Pull the image again; if the problem stays, file an issue."],
        )
    versions: dict[str, Any] = dict(info.versions)
    details["versions"] = versions
    broken = [tool for tool in TOOL_ORDER if not versions.get(tool)]
    lines = [f"Toolchain image {ctx.image}"]
    lines.append(f"  image id: {details['image_id']}")
    lines.append(f"  digest: {details['repo_digest'] or 'none (local build)'}")
    if details["docker_server_version"] is not None:
        mode = "rootless" if details["rootless"] else "rootful"
        lines.append(f"  docker {details['docker_server_version']}, {mode} mode")
    lines += [f"  {tool}: {versions.get(tool)}" for tool in TOOL_ORDER]
    if info.pdk is not None:
        pdk = info.pdk.model_dump()
        details["pdk"] = pdk
        lines.append(
            f"  pdk: {pdk['name']} (open_pdks {pdk['open_pdks'][:8]}), "
            f"{', '.join(pdk['libraries'])}, corner {pdk['corner']}"
        )
    else:
        details["pdk_note"] = (
            "This image has no sky130 files (/opt/pdk/manifest.json is missing), so it is "
            "older than milestone M1. Synthesis needs a newer image."
        )
        lines.append("  pdk: none in this image (older than M1)")
    if broken:
        lines.insert(1, f"NOT READY: no version from {', '.join(broken)}.")
        return ToolResult.from_lines(
            False,
            lines,
            details=details,
            next_hints=[
                f"Pull the image again (`{pull_hint(ctx.image)}`) and check that the disk "
                "holding your temp folder and ~/.cache is not full, then call toolchain_info again."
            ],
        )
    return ToolResult.from_lines(True, lines, details=details)


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool()
    async def toolchain_info() -> ToolResult:
        """Check that Docker and the toolchain image work, and report the version of every tool.

        Call this first if anything else fails with a toolchain error.
        """
        return await asyncio.to_thread(collect, ctx)
