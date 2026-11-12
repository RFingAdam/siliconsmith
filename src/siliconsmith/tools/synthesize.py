# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`synthesize`: turn the design into real sky130 gates and report what came out.

One container call (the worker's `synth` verb) runs the same emit and lint pipeline as
`emit_verilog`, then Yosys with the LibreLane 3.0.14 synthesis order (see
`worker/synth_script.py`). Whatever comes back is untrusted: it is validated with pydantic, the
netlist is read and hashed here, and the cell count and area that decide pass or fail come from
the numbers the worker read from Yosys. The netlist and the statistics are copied into the
tool-owned `build/synth/` folder.

The ledger records every call (stage `synth` = ran to the end, `too_big` = too big for the tile,
`synth_failed` = Yosys or a netlist check failed, `timeout`; or the emit stage that failed).
"""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from siliconsmith.context import ServerContext
from siliconsmith.frontends import Frontend, get_frontend
from siliconsmith.ledger import record_run, snapshot_digests
from siliconsmith.paths import (
    FileTooLargeError,
    MissingFileError,
    atomic_write,
    project_lock,
    safe_read,
)
from siliconsmith.project import Project
from siliconsmith.result import Artifact, ToolResult
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.docker import ToolchainError, remove_run_dirs
from siliconsmith.spec import Spec
from siliconsmith.tools._common import get_project
from siliconsmith.tools.emit_verilog import (
    EmitResult,
    _unreadable,
    handle_outcome,
    precheck,
    read_author,
)
from siliconsmith.tt_sync import clock_period_ns, clock_warnings, sync_for_synthesis
from siliconsmith.utilization import (
    TTConfig,
    UtilizationEstimate,
    estimate_utilization,
    pre_abc_budget_um2,
    read_tt_config,
)
from siliconsmith.worker import limits as lim

# The whole container call (emit, then Yosys). The measured counter takes seconds, a 23,520-cell
# design 400 s (PLAN.md section 6), and designs far over the tile's budget are stopped early.
SYNTH_TIMEOUT_S = 900.0
MAX_NETLIST_BYTES = 16 * 1024 * 1024
MAX_STAT_BYTES = 4 * 1024 * 1024
SUMMARY_FINDINGS = 6
BUILD_DIR = "build/synth"

State = Literal["ran", "too_big", "yosys_failed", "timeout"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Stat(_Strict):
    cells: int = Field(ge=0)
    flip_flops: int = Field(ge=0)
    latch_cells: int = Field(ge=0)
    combinational: int = Field(ge=0)
    area_um2: float = Field(ge=0)


class _Estimate(_Strict):
    generic_area_um2: float = Field(ge=0)
    generic_cells: int = Field(ge=0)
    stop_area_um2: float = Field(ge=0)


class _TopCell(_Strict):
    cell: str = Field(max_length=lim.CELL_NAME_MAX)
    count: int = Field(ge=1)


class _Located(_Strict):
    signal: str = Field(max_length=lim.SYNTH_SIGNAL_MAX)
    file: str = Field(max_length=lim.FILE_MAX)
    line: int = Field(ge=0)
    hdl_file: str = Field(default="", max_length=lim.FILE_MAX)
    hdl_line: int = Field(default=0, ge=0)
    hdl_function: str = Field(default="", max_length=lim.SYNTH_SIGNAL_MAX)


class _Latch(_Located):
    pass


class _Check(_Located):
    kind: Literal["comb_loop", "multiple_drivers", "undriven"]
    message: str = Field(max_length=lim.FINDING_MSG_MAX)


class _Synth(_Strict):
    state: State
    yosys_seconds: float = Field(default=0.0, ge=0)
    message: str | None = Field(default=None, max_length=lim.MESSAGE_MAX)
    log: list[str] = Field(default_factory=list, max_length=lim.MAX_SYNTH_LOG_LINES)
    estimate: _Estimate | None = None
    stat: _Stat | None = None
    top_cells: list[_TopCell] = Field(default_factory=list, max_length=lim.MAX_TOP_CELLS)
    forbidden_cells: list[str] = Field(default_factory=list, max_length=lim.MAX_FORBIDDEN_CELLS)
    latches: list[_Latch] = Field(default_factory=list, max_length=lim.MAX_SYNTH_FINDINGS)
    checks: list[_Check] = Field(default_factory=list, max_length=lim.MAX_SYNTH_FINDINGS)
    netlist: str | None = Field(default=None, max_length=lim.FILE_MAX)
    netlist_sha256: str | None = Field(default=None, max_length=64)


class _SynthResult(EmitResult):
    """The `synth` verb's result: the emit result plus a `synth` entry (None unless emit was ok)."""

    synth: _Synth | None = None


# -- results -------------------------------------------------------------------------------


def _location(item: _Located) -> str:
    where = item.file or "(no file)"
    if item.line:
        where += f":{item.line}"
    if item.hdl_file:
        where += f" (from {item.hdl_file}:{item.hdl_line}, function {item.hdl_function})"
    return where


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
        lines,
        details={
            "stage": stage,
            "frontend": frontend.key,
            "top": f"tt_um_{project.name}",
            **details,
        },
        next_hints=hints,
    )


def _from_emit_failure(emit_result: ToolResult) -> ToolResult:
    """Re-word a failed emit step for `synthesize` (the checks are the same as `emit_verilog`)."""
    lines = emit_result.summary.splitlines()
    head = (
        "synthesize stopped before synthesis: the design did not pass the emit and lint step "
        "(the same checks as emit_verilog)."
    )
    rest = lines[1:] if lines and lines[0].startswith("emit_verilog failed") else lines
    hints = [h.replace("emit_verilog", "synthesize") for h in emit_result.next_hints]
    return ToolResult.from_lines(
        False,
        [head, *rest],
        details=emit_result.details,
        artifacts=emit_result.artifacts,
        next_hints=hints,
    )


def _too_big_hints(tiles: str) -> list[str]:
    return [
        "Make the design smaller: fewer bits, no wide multipliers or big arrays, share one "
        "piece of logic over several clock cycles. Then call synthesize again.",
        f"If the design really needs more room, ask the owner about a bigger tile than {tiles}: "
        "bigger tiles cost more money, so the owner decides. In a headless run, write the "
        "assumption to open_questions in spec.yaml instead of changing tiles.",
    ]


def _early_stop(
    frontend: Frontend,
    project: Project,
    spec: Spec,
    config: TTConfig,
    synth: _Synth,
) -> ToolResult:
    est = synth.estimate
    assert est is not None
    util = dataclasses.replace(
        estimate_utilization(est.generic_area_um2, spec.tiles, config),
        note="rough estimate before technology mapping; layout (M2) gives the real number",
    )
    times = est.generic_area_um2 / util.cell_budget_um2
    lines = [
        f"Too big for {spec.tiles}: synthesis was stopped early, before the gates were mapped.",
        f"The design already needs about {est.generic_area_um2:.0f} um2 of cells "
        f"(rough, {est.generic_cells} basic gates and flip-flops); a {spec.tiles} tile has room "
        f"for about {util.cell_budget_um2:.0f} um2, so it is roughly {times:.1f} times too big.",
        f"Stopped after {synth.yosys_seconds:.0f} s; no netlist was written.",
    ]
    return _failure(
        frontend,
        project,
        lines,
        stage="too_big",
        hints=_too_big_hints(spec.tiles),
        synth={"tiles": spec.tiles, "verdict": "too_big"},
        utilization=util.as_dict(),
        estimate=est.model_dump(),
        yosys_seconds=synth.yosys_seconds,
        clock_period_ns=clock_period_ns(spec.clock_hz),
        warnings=clock_warnings(spec.clock_hz),
    )


def _yosys_failure(frontend: Frontend, project: Project, synth: _Synth) -> ToolResult:
    lines = ["Synthesis failed: Yosys stopped with an error.", synth.message or "", *synth.log]
    hint = (
        "Fix the file and line named above with write_rtl, then call synthesize again."
        if synth.log
        else "Call toolchain_info and check that the toolchain image matches this version."
    )
    return _failure(
        frontend,
        project,
        [ln for ln in lines if ln],
        stage="synth_failed",
        hints=[hint],
        log=list(synth.log),
        message=synth.message,
        yosys_seconds=synth.yosys_seconds,
    )


def _plain_cell(cell: str) -> str:
    return cell.removeprefix("sky130_fd_sc_hd__")


def _read_synth_files(
    project: Project, outcome: WorkerOutcome, synth: _Synth, top: str
) -> tuple[bytes, bytes] | str:
    """The netlist and `stat.json` bytes from the run folder, or the reason they cannot be used."""
    if outcome.run_id is None:
        return "the runner reported no run folder"
    base = f"build/.ss/{outcome.run_id}/out/synth"
    try:
        netlist = safe_read(project.dir, f"{base}/{top}.v", MAX_NETLIST_BYTES, allow_hidden=True)
        stats = safe_read(
            project.dir, f"{base}/reports/stat.json", MAX_STAT_BYTES, allow_hidden=True
        )
    except MissingFileError as exc:
        return f"a result file is missing ({exc})"
    except FileTooLargeError as exc:
        return f"a result file is too large ({exc})"
    except ToolError as exc:
        return f"a result file cannot be read ({exc})"
    if f"module {top}".encode() not in netlist:
        return f"the netlist has no module {top}"
    return netlist, stats


def _ran(
    ctx: ServerContext,
    project: Project,
    frontend: Frontend,
    spec: Spec,
    config: TTConfig,
    synth: _Synth,
    outcome: WorkerOutcome,
    emit_result: ToolResult,
) -> ToolResult:
    assert synth.stat is not None and synth.estimate is not None
    top = f"tt_um_{project.name}"
    files = _read_synth_files(project, outcome, synth, top)
    if isinstance(files, str):
        return _unreadable(frontend, project, outcome, files)
    netlist, stats = files
    sha = hashlib.sha256(netlist).hexdigest()
    netlist_rel, stats_rel = f"{BUILD_DIR}/{top}.v", f"{BUILD_DIR}/stat.json"
    atomic_write(project.dir, netlist_rel, netlist)
    atomic_write(project.dir, stats_rel, stats)

    stat = synth.stat
    util: UtilizationEstimate = estimate_utilization(stat.area_um2, spec.tiles, config)
    warnings = list(clock_warnings(spec.clock_hz))
    for latch in synth.latches:
        name = latch.signal or "a signal"
        warnings.append(
            f"Latch for {name} at {_location(latch)}: the design holds a value without a clock "
            "(an always_latch). Tiny Tapeout allows it, but latches are easy to get wrong."
        )
    if util.verdict == "warn":
        warnings.append("Estimated utilization is above 90%: placement may struggle.")

    problems: list[str] = []
    for cell in synth.forbidden_cells:
        problems.append(
            f"The netlist uses {_plain_cell(cell)}, a cell Tiny Tapeout's flow does not allow."
        )
    for check in synth.checks:
        problems.append(f"{_location(check)}: {check.kind.replace('_', ' ')}: {check.message}")
    too_big = util.verdict == "too_big"

    details: dict[str, Any] = {
        "stage": "synth",
        "frontend": frontend.key,
        "top": top,
        "cells": stat.cells,
        "flip_flops": stat.flip_flops,
        "combinational": stat.combinational,
        "area_um2": round(stat.area_um2, 3),
        "top_cells": [c.model_dump() for c in synth.top_cells],
        "utilization": util.as_dict(),
        "latches": [item.model_dump() for item in synth.latches],
        "checks": [item.model_dump() for item in synth.checks],
        "forbidden_cells": list(synth.forbidden_cells),
        "netlist": netlist_rel,
        "netlist_sha256": sha,
        "yosys_seconds": synth.yosys_seconds,
        "clock_period_ns": clock_period_ns(spec.clock_hz),
        "warnings": warnings,
        "synth": {
            "cells": stat.cells,
            "flip_flops": stat.flip_flops,
            "area_um2": round(stat.area_um2, 3),
            "utilization": round(util.estimate, 4),
            "tiles": spec.tiles,
            "verdict": util.verdict,
            "netlist_sha256": sha,
        },
    }
    artifacts = [
        *emit_result.artifacts,
        Artifact(path=netlist_rel, kind="netlist"),
        Artifact(path=stats_rel, kind="report"),
    ]
    pct = f"{util.estimate * 100:.0f}%"
    count = (
        f"{stat.cells} cells ({stat.flip_flops} flip-flops, {stat.combinational} logic"
        + (f", {stat.latch_cells} latches" if stat.latch_cells else "")
        + f"), {stat.area_um2:.1f} um^2"
    )
    size = f"about {pct} of a {spec.tiles} tile ({util.note})"
    if problems or too_big:
        lines = [f"Synthesis found problems: {count}, {size}."]
        if too_big:
            lines.append(
                f"Too big for {spec.tiles}: the cells need {util.cell_area_um2:.0f} um2 and the "
                f"tile has room for about {util.cell_budget_um2:.0f} um2."
            )
        lines += [*problems[:SUMMARY_FINDINGS]]
        if len(problems) > SUMMARY_FINDINGS:
            lines.append(f"... {len(problems) - SUMMARY_FINDINGS} more in details.checks")
        lines += [f"Warning: {w}" for w in warnings[:3]]
        hints = _too_big_hints(spec.tiles) if too_big else []
        if synth.checks:
            hints.append(
                "Fix the first problem (file and line above) with write_rtl, then call "
                "synthesize again."
            )
        if synth.forbidden_cells:
            hints.append("Rewrite the logic that needs that cell, then call synthesize again.")
        details["stage"] = "too_big" if too_big else "synth_failed"
        return ToolResult.from_lines(
            False, lines, details=details, artifacts=artifacts, next_hints=hints
        )
    top_text = ", ".join(f"{_plain_cell(c.cell)} x{c.count}" for c in synth.top_cells[:5])
    lines = [f"Synthesis passed: {count}, {size}."]
    if top_text:
        lines.append(f"Most used cells: {top_text}.")
    lines += [f"Warning: {w}" for w in warnings[:4]]
    lines.append(
        f"Netlist: {netlist_rel} (sha256 {sha[:12]}...). Real size and timing: layout (M2)."
    )
    hints = ["Call status to see the checks that are still open."]
    if util.verdict == "warn":
        hints.insert(0, "The design nearly fills the tile: consider making it smaller.")
    return ToolResult.from_lines(
        True, lines, details=details, artifacts=artifacts, next_hints=hints
    )


def handle_synth_outcome(
    ctx: ServerContext,
    project: Project,
    frontend: Frontend,
    author: str,
    spec: Spec,
    config: TTConfig,
    outcome: WorkerOutcome,
) -> ToolResult:
    if outcome.timed_out:
        return _failure(
            frontend,
            project,
            [
                f"Synthesis ran out of time: the container call did not finish in "
                f"{SYNTH_TIMEOUT_S:.0f} s and was stopped.",
                "The design is probably far too big or too complicated for Yosys to finish.",
            ],
            stage="timeout",
            hints=[
                "Make the design smaller (fewer bits, no wide multipliers or large arrays) and "
                "call synthesize again.",
            ],
            timed_out=True,
        )
    emit_result = handle_outcome(ctx, project, frontend, author, outcome)
    if not emit_result.ok:
        return _from_emit_failure(emit_result)
    assert outcome.result is not None
    try:
        result = _SynthResult.model_validate(outcome.result)
    except ValidationError:
        return _unreadable(frontend, project, outcome, "the synthesis result has the wrong shape")
    synth = result.synth
    if synth is None:
        return _unreadable(frontend, project, outcome, "the result has no synthesis part")
    if synth.state == "timeout":
        return _failure(
            frontend,
            project,
            [
                "Synthesis ran out of time: " + (synth.message or "Yosys did not finish") + ".",
                f"It was stopped after {synth.yosys_seconds:.0f} s "
                f"(limit {SYNTH_TIMEOUT_S:.0f} s).",
            ],
            stage="timeout",
            hints=["Make the design smaller and call synthesize again."],
            timed_out=True,
            yosys_seconds=synth.yosys_seconds,
        )
    if synth.state == "yosys_failed":
        return _yosys_failure(frontend, project, synth)
    if synth.estimate is None:
        return _unreadable(frontend, project, outcome, "the result has no size estimate")
    if synth.state == "too_big":
        return _early_stop(frontend, project, spec, config, synth)
    if synth.stat is None or synth.netlist is None:
        return _unreadable(frontend, project, outcome, "the result has no statistics")
    return _ran(ctx, project, frontend, spec, config, synth, outcome, emit_result)


def synthesize(ctx: ServerContext, project_name: str) -> ToolResult:
    project = get_project(ctx, project_name)
    frontend = get_frontend(project.frontend)
    with project_lock(project.dir):
        spec = sync_for_synthesis(project)  # a ToolError if spec.yaml cannot be used
        config = read_tt_config(project.dir)
        before = snapshot_digests(project, ctx.image)
        result = _synthesize_locked(ctx, project, frontend, spec, config)
        return record_run(project, ctx.image, "synthesize", result, before)


def _synthesize_locked(
    ctx: ServerContext, project: Project, frontend: Frontend, spec: Spec, config: TTConfig
) -> ToolResult:
    author = read_author(project)
    early = precheck(project, frontend)
    if early is not None:
        return _from_emit_failure(early)
    request: dict[str, Any] = {
        **frontend.worker_request(project.name, author),
        "clock_period_ns": clock_period_ns(spec.clock_hz),
        "stop_area_um2": pre_abc_budget_um2(spec.tiles, config),
        "timeout_s": SYNTH_TIMEOUT_S,
    }
    try:
        try:
            outcome = ctx.get_runner().run(
                "synth", request, project_dir=project.dir, timeout_s=SYNTH_TIMEOUT_S
            )
        except ToolchainError as exc:
            raise ToolError(str(exc)) from exc
        return handle_synth_outcome(ctx, project, frontend, author, spec, config, outcome)
    finally:
        remove_run_dirs(project.dir / "build")


DESCRIPTION = """\
Turn the design into real chip gates and say how big it is. Synthesis is the step where a \
program (Yosys) replaces your RTL with the small ready-made logic gates and flip-flops of the \
sky130 chip library, the same way Tiny Tapeout's own flow does. The tool first runs the same \
checks as emit_verilog (a lint problem stops it, with file and line), then synthesizes at the \
clock speed in spec.yaml. The summary gives the number of cells (flip-flops, which store bits, \
and logic gates), the total cell area in um^2, the most used cell types, and the estimated \
utilization: how much of your tile's usable area the cells would fill. Utilization is an \
ESTIMATE; real placement (layout, milestone M2) gives the real number, and it needs headroom \
(above 90% is risky, above 100% will not fit). The tile size comes from `tiles` in spec.yaml \
(default 1x1); a design far too big for it is stopped early with a "too big" message and what \
to do: make the design smaller, or ask the owner about a bigger tile (it costs money, so the \
owner decides). ok is false for a lint or Yosys error, a combinational loop, a signal driven \
twice, an undriven output, a cell that Tiny Tapeout does not allow, a design that is too big, \
or a time-out (900 s). A latch you wrote on purpose (always_latch) is reported as a warning \
with file and line. A clock above 66 MHz also warns: Tiny Tapeout's pins cannot go that fast. \
The netlist is saved in build/synth/. Usually takes seconds to a minute.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="synthesize",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    async def synthesize_tool(project: str) -> ToolResult:
        return await asyncio.to_thread(synthesize, ctx, project)
