# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`write_test`: write a cocotb test file."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.covers import parse_covers
from siliconsmith.paths import Category
from siliconsmith.result import ToolResult, make_summary
from siliconsmith.tools._common import get_project, read_spec_ids, write_text_file

MAX_DETAIL_ITEMS = 50
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
        result = write_text_file(
            ctx,
            tool="write_test",
            accepts=ACCEPTS,
            project_name=project,
            path=path,
            content=content,
        )
        if not path.endswith(".py"):
            return result
        # Static only: the test file is parsed with ast and never imported or run here.
        spec_ids = read_spec_ids(get_project(ctx, project))
        report = parse_covers(content, path, spec_ids)
        if report.error is not None:
            return result  # the syntax-error report from write_text_file already says it
        covers = {t.name: list(t.covers) for t in report.tests[:MAX_DETAIL_ITEMS]}
        result.details["covers"] = covers
        result.details["covers_warnings"] = report.warnings[:MAX_DETAIL_ITEMS]
        result.details["covers_warnings_total"] = len(report.warnings)
        result.details["tests"] = [t.as_dict() for t in report.tests[:MAX_DETAIL_ITEMS]]
        result.details["tests_total"] = len(report.tests)
        lines = result.summary.splitlines()
        if report.tests:
            lines.append(
                "Covers: "
                + "; ".join(f"{t.name} -> {', '.join(t.covers) or '(none)'}" for t in report.tests)
            )
        else:
            lines.append("No cocotb tests (@cocotb.test) found in this file.")
        if spec_ids is None:
            lines.append("Requirement ids were not checked: no valid spec.yaml (use write_spec).")
        lines.extend(f"Warning: {w}" for w in report.warnings)
        result.summary = make_summary(lines)
        return result
