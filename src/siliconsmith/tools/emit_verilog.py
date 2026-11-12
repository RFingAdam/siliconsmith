# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`emit_verilog`: turn the design source into a checked Verilog core plus the TT wrapper.

One container call (the worker's `emit` verb) elaborates the design, reads and checks the ports,
generates the wrapper and lints everything. Whatever comes back is untrusted: it is validated
with pydantic, the ports are checked again here, and `src/project.v` is regenerated on the host
rather than copied. A generated core is copied to `src/<name>.v`; a hand-written Verilog core is
never touched.
"""

from __future__ import annotations

import ast
import asyncio
from typing import Any, Literal

import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from siliconsmith.context import ServerContext
from siliconsmith.frontends import Frontend, get_frontend
from siliconsmith.paths import (
    FileTooLargeError,
    MissingFileError,
    atomic_write,
    project_lock,
    safe_read,
)
from siliconsmith.project import Project, validate_text
from siliconsmith.result import Artifact, ToolResult
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.docker import ToolchainError, remove_run_dirs
from siliconsmith.tools._common import MAX_WRITE_BYTES, get_project
from siliconsmith.worker import limits as lim
from siliconsmith.worker import pyerror
from siliconsmith.worker.wrapper_gen import TT_PORTS, check_core_ports, describe_port_problems

EMIT_TIMEOUT_S = 300.0
MAX_INFO_BYTES = 64 * 1024
SUMMARY_FINDINGS = 8

Stage = Literal["elaborate", "ports", "lint", "done"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Port(_Strict):
    direction: Literal["input", "output", "inout"]
    width: int = Field(ge=1, le=4096)


class _Finding(_Strict):
    severity: Literal["warning", "error"]
    code: str = Field(max_length=lim.FINDING_CODE_MAX)
    file: str = Field(max_length=lim.FILE_MAX)
    line: int = Field(ge=0)
    col: int = Field(ge=0)
    msg: str = Field(max_length=lim.FINDING_MSG_MAX)
    detail: str = Field(default="", max_length=lim.FINDING_DETAIL_MAX)


class _PythonError(_Strict):
    kind: str = Field(max_length=lim.PYERR_KIND_MAX)
    file: str = Field(max_length=lim.FILE_MAX)
    line: int | None = Field(default=None, ge=1)
    col: int | None = Field(default=None, ge=0)
    msg: str = Field(max_length=lim.PYERR_MSG_MAX)
    source: str | None = Field(default=None, max_length=lim.PYERR_SOURCE_MAX)


class _WorkerError(_Strict):
    kind: str = Field(default="", max_length=100)
    message: str = Field(default="", max_length=1000)


class _EmitResult(_Strict):
    """The `emit` verb's result, as the host accepts it."""

    ok: bool
    stage: Stage | None = None
    ports: dict[str, _Port] | None = None
    wrapper_ports: dict[str, _Port] | None = None
    lint: list[_Finding] = Field(default_factory=list, max_length=lim.MAX_FINDINGS)
    lint_total: int = Field(default=0, ge=0)
    python_error: _PythonError | None = None
    message: str | None = Field(default=None, max_length=lim.MESSAGE_MAX)
    core_generated: bool = False
    error: _WorkerError | None = None


def read_author(project: Project) -> str:
    raw = safe_read(project.dir, "info.yaml", MAX_INFO_BYTES)
    try:
        info = yaml.safe_load(raw)
        author = info["project"]["author"]
    except (yaml.YAMLError, KeyError, TypeError) as exc:
        raise ToolError(
            f"{project.name}/info.yaml has no project.author text ({type(exc).__name__}); "
            "it is written by create_project and should not be edited"
        ) from exc
    return validate_text("author", str(author), 100)


EmitResult = _EmitResult


# -- results -------------------------------------------------------------------------------


def _failure(
    frontend: Frontend,
    project: Project,
    lines: list[str],
    *,
    stage: str,
    hints: list[str],
    **details: Any,
) -> ToolResult:
    return ToolResult.from_lines(
        False,
        ["emit_verilog failed.", *lines],
        details={
            "stage": stage,
            "frontend": frontend.key,
            "top": f"tt_um_{project.name}",
            **details,
        },
        next_hints=hints,
    )


def _python_failure(frontend: Frontend, project: Project, error: dict[str, Any]) -> ToolResult:
    where = error["file"] + (f":{error['line']}" if error.get("line") else "")
    return _failure(
        frontend,
        project,
        [
            f"Stage: elaborate (turning {frontend.entry(project.name)} into Verilog).",
            *pyerror.format_lines(error),
        ],
        stage="elaborate",
        hints=[
            f"Fix {where} ({error['kind']}) with write_rtl, then call emit_verilog again.",
        ],
        python_error=error,
    )


def precheck(project: Project, frontend: Frontend) -> ToolResult | None:
    """Read the entry file; a Python file with a syntax error fails here without a container."""
    entry = frontend.entry(project.name)
    try:
        raw = safe_read(project.dir, entry, MAX_WRITE_BYTES)
    except MissingFileError:
        raise ToolError(
            f"{entry!r} does not exist in project {project.name!r}; write it with write_rtl first"
        ) from None
    if not frontend.is_python:
        return None
    try:
        ast.parse(raw.decode("utf-8"), filename=entry)
    except SyntaxError as exc:
        error = pyerror.extract(exc, entry)
        error["file"] = entry
        return _python_failure(frontend, project, error)
    except UnicodeDecodeError:
        return _python_failure(
            frontend, project, pyerror.design_error(entry, "not valid UTF-8 text")
        )
    except (RecursionError, MemoryError, ValueError) as exc:
        return _python_failure(
            frontend,
            project,
            pyerror.design_error(entry, f"the file could not be parsed ({type(exc).__name__})"),
        )
    return None


def _ports_line(ports: dict[str, _Port]) -> str:
    short = {"input": "in", "output": "out", "inout": "inout"}
    return ", ".join(f"{n} {short[p.direction]} {p.width}" for n, p in ports.items())


def _finding_line(item: _Finding) -> str:
    where = item.file or "(no file)"
    if item.line:
        where += f":{item.line}" + (f":{item.col}" if item.col else "")
    return f"{where}: {item.severity} {item.code}: {item.msg}"


def _unreadable(
    frontend: Frontend, project: Project, outcome: WorkerOutcome, why: str
) -> ToolResult:
    tail = pyerror.map_paths(outcome.stderr_tail.strip())[-1200:]
    lines = [f"The container call returned an unreadable result ({why})."]
    if outcome.exit_code != 0:
        lines.append(f"The container exited with code {outcome.exit_code}.")
    lines += tail.splitlines()[-6:]
    return _failure(
        frontend,
        project,
        lines,
        stage="elaborate",
        hints=[
            "If the design allocates a lot of memory or crashes the interpreter, simplify it. "
            "Otherwise call toolchain_info and check the toolchain image.",
        ],
        reason=why,
        exit_code=outcome.exit_code,
        stderr_tail=tail,
    )


def _check_generated_core(name: str, text: str) -> str | None:
    if len(text.encode("utf-8")) > MAX_WRITE_BYTES:
        return f"the generated Verilog is larger than {MAX_WRITE_BYTES} bytes"
    if f"module {name}" not in text:
        return f"the generated Verilog has no 'module {name}'"
    if "/work" in text or "/home" in text:
        return "the generated Verilog contains an absolute path (/work or /home)"
    return None


def _hints_for_lint(frontend: Frontend, project: Project, findings: list[_Finding]) -> list[str]:
    first = findings[0]
    editable = frontend.entry(project.name)
    if first.file == "src/project.v":
        return ["The wrapper is generated; fix the core so that the finding goes away."]
    if frontend.generated:
        return [
            f"Fix the first finding ({first.code}, {first.file}:{first.line}) in {editable}: "
            f"the Verilog in src/{project.name}.v is generated from it. Then call emit_verilog."
        ]
    return [
        f"Fix the first finding ({first.code}, {first.file}:{first.line}) in {editable} with "
        "write_rtl, then call emit_verilog again."
    ]


def _success_or_lint(
    ctx: ServerContext,
    project: Project,
    frontend: Frontend,
    author: str,
    result: _EmitResult,
    outcome: WorkerOutcome,
) -> ToolResult:
    """The pipeline reached the lint step: write the files and report the lint outcome."""
    from siliconsmith.wrapper import generate_wrapper

    assert result.ports is not None and result.wrapper_ports is not None
    name = project.name
    ports = {n: (p.direction, p.width) for n, p in result.ports.items()}
    problems = check_core_ports(ports)
    wrapper = {n: (p.direction, p.width) for n, p in result.wrapper_ports.items()}
    if wrapper != dict(TT_PORTS):
        problems.append("the wrapper ports reported by the container are not the TT pin set")
    if problems:
        return _failure(
            frontend,
            project,
            ["The ports do not fit Tiny Tapeout:", *describe_port_problems(problems).splitlines()],
            stage="ports",
            hints=[f"Fix the ports of the core in {frontend.entry(name)}, then call emit_verilog."],
            ports={n: p.model_dump() for n, p in result.ports.items()},
        )
    if frontend.generated:
        if outcome.run_id is None:
            return _unreadable(frontend, project, outcome, "the runner reported no run folder")
        rel = f"build/.ss/{outcome.run_id}/out/{name}.v"
        try:
            text = safe_read(project.dir, rel, lim.MAX_CORE_READ_BYTES, allow_hidden=True).decode(
                "utf-8", errors="replace"
            )
        except MissingFileError:
            return _unreadable(frontend, project, outcome, "the generated Verilog is missing")
        except FileTooLargeError:
            text = ""
            oversized = True
        else:
            oversized = False
        reason = (
            f"the generated Verilog is larger than {MAX_WRITE_BYTES} bytes"
            if oversized
            else _check_generated_core(name, text)
        )
        if reason:
            return _failure(
                frontend,
                project,
                [f"The generated Verilog was not accepted: {reason}."],
                stage="elaborate",
                hints=[f"Check {frontend.entry(name)} for strings that hold absolute paths."],
            )
        atomic_write(project.dir, f"src/{name}.v", text.encode("utf-8"))
    wrapper_text = generate_wrapper(name, {n: d for n, (d, _) in ports.items()}, author)
    atomic_write(project.dir, "src/project.v", wrapper_text.encode("utf-8"))

    core_kind = "generated" if frontend.generated else "rtl"
    artifacts = [
        Artifact(path=f"src/{name}.v", kind=core_kind),
        Artifact(path="src/project.v", kind="wrapper"),
    ]
    details: dict[str, Any] = {
        "stage": result.stage,
        "frontend": frontend.key,
        "top": f"tt_um_{name}",
        "ports": {n: p.model_dump() for n, p in result.ports.items()},
        "wrapper_ports": {n: p.model_dump() for n, p in result.wrapper_ports.items()},
        "lint": [f.model_dump() for f in result.lint],
        "lint_total": result.lint_total,
    }
    source_note = (
        f"src/{name}.v is generated from {frontend.entry(name)}; do not edit it."
        if frontend.generated
        else f"src/{name}.v is your hand-written core (left untouched)."
    )
    if not result.lint:
        lines = [
            f"emit_verilog ok: {frontend.language} design {name!r}.",
            f"Top module: tt_um_{name} (src/project.v, generated).",
            f"Core ports: {_ports_line(result.ports)}",
            "Lint: clean (verilator -Wall).",
            source_note,
        ]
        return ToolResult.from_lines(True, lines, details=details, artifacts=artifacts)
    shown = result.lint[:SUMMARY_FINDINGS]
    lines = [
        "emit_verilog failed at the lint step: Verilog was generated but has "
        f"{result.lint_total} lint finding(s).",
        f"Core ports: {_ports_line(result.ports)}",
        *(_finding_line(item) for item in shown),
    ]
    if result.lint_total > len(shown):
        lines.append(f"… {result.lint_total - len(shown)} more in details.lint")
    lines.append(source_note)
    return ToolResult.from_lines(
        False,
        lines,
        details=details,
        artifacts=artifacts,
        next_hints=_hints_for_lint(frontend, project, result.lint),
    )


def handle_outcome(
    ctx: ServerContext,
    project: Project,
    frontend: Frontend,
    author: str,
    outcome: WorkerOutcome,
) -> ToolResult:
    if outcome.timed_out:
        return _failure(
            frontend,
            project,
            [f"elaboration did not finish in {EMIT_TIMEOUT_S:.0f} s."],
            stage="elaborate",
            hints=[
                f"Look for an endless loop or a huge computation in {frontend.entry(project.name)} "
                "that runs while the design is built, not while it simulates."
            ],
            timed_out=True,
        )
    if outcome.result is None:
        return _unreadable(frontend, project, outcome, "the container wrote no result")
    try:
        result = _EmitResult.model_validate(outcome.result)
    except ValidationError:
        return _unreadable(frontend, project, outcome, "the result has the wrong shape")
    if result.error is not None or result.stage is None:
        detail = f"{result.error.kind}: {result.error.message}" if result.error else "no stage"
        raise ToolError(
            f"the container call failed inside ({detail}). Call toolchain_info to check "
            "that the toolchain image matches this version of Siliconsmith; if it does, file "
            "an issue."
        )
    if result.ok != (result.stage == "done") or result.lint_total < len(result.lint):
        return _unreadable(frontend, project, outcome, "the result contradicts itself")
    if result.stage in ("lint", "done"):
        # `lint` with no ports is a Verilog syntax error found before the ports could be read.
        if result.ports is None and result.wrapper_ports is None and result.lint:
            return _core_syntax_failure(project, frontend, result)
        if result.ports is None or result.wrapper_ports is None:
            return _unreadable(frontend, project, outcome, "the result has no ports")
        return _success_or_lint(ctx, project, frontend, author, result, outcome)
    if result.python_error is not None:
        error = result.python_error.model_dump()
        return _python_failure(frontend, project, error)
    message = result.message or "no message"
    return _failure(
        frontend,
        project,
        ["Stage: ports (reading the core's ports with Yosys).", *message.splitlines()[:12]],
        stage=result.stage,
        hints=[
            f"Fix the ports of the core in {frontend.entry(project.name)}, then call emit_verilog."
        ],
        ports={n: p.model_dump() for n, p in (result.ports or {}).items()},
        message=message,
    )


def _core_syntax_failure(project: Project, frontend: Frontend, result: _EmitResult) -> ToolResult:
    shown = result.lint[:SUMMARY_FINDINGS]
    return _failure(
        frontend,
        project,
        ["Stage: lint (the Verilog core does not parse).", *(_finding_line(f) for f in shown)],
        stage="lint",
        hints=_hints_for_lint(frontend, project, result.lint),
        lint=[f.model_dump() for f in result.lint],
        lint_total=result.lint_total,
    )


def emit(ctx: ServerContext, project_name: str) -> ToolResult:
    project = get_project(ctx, project_name)
    frontend = get_frontend(project.frontend)
    with project_lock(project.dir):
        author = read_author(project)
        early = precheck(project, frontend)
        if early is not None:
            return early
        try:
            try:
                outcome = ctx.get_runner().run(
                    "emit",
                    frontend.worker_request(project.name, author),
                    project_dir=project.dir,
                    timeout_s=EMIT_TIMEOUT_S,
                )
            except ToolchainError as exc:
                raise ToolError(str(exc)) from exc
            return handle_outcome(ctx, project, frontend, author, outcome)
        finally:
            remove_run_dirs(project.dir / "build")


DESCRIPTION = """\
Turn the project's design into Verilog and check it. For Amaranth and MyHDL this runs your \
hdl/<name>.py in a container and writes the generated core to src/<name>.v; a Verilog project's \
src/<name>.v is used as it is. The tool then checks the core's ports against the Tiny Tapeout \
pins (names, directions, widths), regenerates the tt_um_<name> wrapper in src/project.v, and \
lints everything with Verilator (-Wall; latches are errors) and Yosys \
`check -assert` (multiple drivers and logic loops are errors). ok is false \
for a Python error (with file and line), bad ports, or any lint finding; details.lint lists \
{severity, code, file, line, col, msg}. Takes a few seconds to a minute; the first call is the \
slowest.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="emit_verilog",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    async def emit_verilog(project: str) -> ToolResult:
        return await asyncio.to_thread(emit, ctx, project)
