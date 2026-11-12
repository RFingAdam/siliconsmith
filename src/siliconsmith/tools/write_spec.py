# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`write_spec`: check and save `spec.yaml`, the single source of clock, pins and requirements."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.paths import atomic_write, project_lock
from siliconsmith.result import Artifact, ToolResult
from siliconsmith.spec import MAX_ERRORS, SPEC_NAME, Spec, SpecError, parse_spec
from siliconsmith.tools._common import get_project

DESCRIPTION = """\
Check and save spec.yaml, the short spec of the chip. It is the only source of the clock, the \
pin meanings and the requirements, and the owner reads it to judge what you plan to build. Send \
the full file as YAML text in content; it is saved exactly as you wrote it (comments are kept) \
only if it is valid, and an invalid file is not saved.

Format (version 1; unknown keys are rejected):
  version: 1                      # optional, must be 1
  title: "One line name"
  summary: "What the chip does and why, in plain English."
  clock_hz: 10000000              # required, integer > 0
  reset: "rst_n, active-low, synchronous"   # plain English; write 'none' if there is no reset
  pins:                           # optional; bit numbers 0-7 as integer keys
    ui:  {0: "enable", 1: "mode"}         # inputs ui_in[bit]
    uo:  {0: "count bit 0"}               # outputs uo_out[bit]
    uio: {}                               # bidirectional uio[bit]
  requirements:                   # at least one
    - id: R1                      # R followed by a number, unique: R1, R2, R10
      text: "The counter adds 1 each clock while enable is high."
      limit: {min: 0, typ: 1, max: 2, unit: "V"}   # optional; at least one of min/typ/max
  open_questions:                 # optional; write your assumptions here
    - "Assumed 10 MHz; please confirm."

On errors ok is false and details.errors lists {path, message} (for example pins.ui.8 or \
requirements[1].id). Fix them and call again. Then write tests whose docstrings say \
'Covers: R1' for the requirement ids.
"""


def _summary_lines(errors: list[SpecError]) -> list[str]:
    count = len(errors)
    head = f"{SPEC_NAME} was NOT saved: {count} problem{'s' if count != 1 else ''}."
    return [head, *(f"  {e.path}: {e.message}" for e in errors)]


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="write_spec",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    def write_spec(project: str, content: str) -> ToolResult:
        found = get_project(ctx, project)
        try:
            data = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolError(f"content is not valid UTF-8 text: {exc.reason}") from exc
        parsed = parse_spec(content)
        if not isinstance(parsed, Spec):
            errors = parsed[:MAX_ERRORS]
            return ToolResult.from_lines(
                False,
                _summary_lines(errors),
                details={"errors": [e.as_dict() for e in errors]},
                next_hints=[
                    f"Fix the listed fields and call write_spec again with the full {SPEC_NAME}."
                ],
            )
        with project_lock(found.dir):
            atomic_write(found.dir, SPEC_NAME, data)
        questions = len(parsed.open_questions)
        lines = [
            f"Saved {SPEC_NAME}: {parsed.title!r}.",
            f"Clock {parsed.clock_hz} Hz, {len(parsed.requirements)} requirement"
            f"{'s' if len(parsed.requirements) != 1 else ''}, "
            f"{questions} open question{'s' if questions != 1 else ''}.",
        ]
        return ToolResult.from_lines(
            True,
            lines,
            details={
                "path": SPEC_NAME,
                "bytes": len(data),
                "title": parsed.title,
                "clock_hz": parsed.clock_hz,
                "requirement_ids": parsed.requirement_ids(),
                "open_questions": questions,
            },
            artifacts=[Artifact(path=SPEC_NAME, kind="spec")],
            next_hints=[
                "Write tests with write_test; give each test a docstring line such as "
                "'Covers: R1, R2' naming the requirement ids above.",
            ],
        )
