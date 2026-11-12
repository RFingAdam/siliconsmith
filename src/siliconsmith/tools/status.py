# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`status`: show which validation steps pass for the project's current files."""

from __future__ import annotations

import asyncio

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.result import Artifact, ToolResult
from siliconsmith.status import DOC_REL, StatusReport, refresh_status
from siliconsmith.tools._common import get_project

DESCRIPTION = """\
Show the validation sequence for the project: for each step (spec valid, spec approved, Verilog \
check, simulation on Icarus and on Verilator, requirement coverage, synthesis, and the later \
milestones) whether it is pass, fail, stale (a file it depends on changed since it ran), not \
run, or not available. It also writes the same table to docs/verification.md. It reads the \
ledger that emit_verilog, run_tests and synthesize keep; it never runs a simulation. Approving \
the spec is the owner's step on the command line; no tool can do it, so do not try.
"""


def build_result(report: StatusReport) -> ToolResult:
    steps = [s.as_dict() for s in report.steps]
    hints = []
    for step in report.steps:
        if step.state in ("fail", "stale", "not_run") and step.key != "spec_approved":
            hints.append(f"Next: {step.title}: {step.evidence}")
            break
    return ToolResult.from_lines(
        not any(s.state == "fail" for s in report.steps),
        report.summary_lines(),
        details={"steps": steps, "ledger_problem": report.ledger_problem},
        artifacts=[Artifact(path=DOC_REL, kind="doc")],
        next_hints=hints,
    )


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="status",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False),
    )
    async def status(project: str) -> ToolResult:
        proj = get_project(ctx, project)
        report = await asyncio.to_thread(refresh_status, proj, ctx.image)
        return build_result(report)
