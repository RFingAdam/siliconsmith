# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`check_equivalence`: prove (or fail to prove) that the sky130 netlist does what the RTL does.

One container call (the worker's `equiv` verb, `worker/equiv.py`): the same emit and lint as
`emit_verilog`, then the PRODUCTION synthesis of `synthesize` (same script, early stop and netlist
checks; the netlist stays in the run folder), then the equivalence check on that netlist file.
There is no way to hand the tool a different netlist. What comes back is untrusted: it is
validated with pydantic, the netlist is read and hashed here and must be the one the worker
checked, and `ok` is decided from the verdict alone: only `equivalent` is ok.

The ledger records every call (stage `equivalent`, `not_equivalent`, `not_proven`, `equiv_error`;
or `synth_failed`, `too_big`, `timeout`; or the emit stage that failed) with the verdict, the
reason, the method and the netlist hash, and the synthesis digest the check depends on.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from siliconsmith.context import ServerContext
from siliconsmith.frontends import Frontend, get_frontend
from siliconsmith.ledger import record_run, snapshot_digests
from siliconsmith.paths import project_lock
from siliconsmith.project import Project
from siliconsmith.result import ToolResult
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
from siliconsmith.tools.synthesize import (
    SYNTH_TIMEOUT_S,
    _early_stop,
    _location,
    _plain_cell,
    _read_synth_files,
    _Synth,
    _yosys_failure,
)
from siliconsmith.tt_sync import clock_period_ns, clock_warnings, sync_for_synthesis
from siliconsmith.utilization import (
    TTConfig,
    estimate_utilization,
    pre_abc_budget_um2,
    read_tt_config,
)
from siliconsmith.worker import limits as lim
from siliconsmith.worker.equiv import BUDGET_S, DEADLINE_S, NOT_A_PASS
from siliconsmith.worker.equiv_parse import show_bits

# The check's own limit (worker: 300 s), and the whole container call: the synthesis part, the
# check and a margin for emit and for reading the results.
EQUIV_DEADLINE_S = DEADLINE_S
RUN_MARGIN_S = 60.0
EQUIV_TIMEOUT_S = SYNTH_TIMEOUT_S + EQUIV_DEADLINE_S + RUN_MARGIN_S
SUMMARY_FINDINGS = 6
SUMMARY_TRACE_ROWS = 6
SIGNAL_KEYS = 8

Verdict = Literal["equivalent", "not_equivalent", "not_proven", "error"]
_Bits = Annotated[str, Field(max_length=lim.EQUIV_VALUE_MAX)]
_Named = dict[Annotated[str, Field(max_length=40)], _Bits]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Diff(_Strict):
    signal: str = Field(max_length=lim.EQUIV_SIGNAL_MAX)
    rtl: _Bits | None = None
    netlist: _Bits | None = None


class _Mismatch(_Strict):
    cycle: int = Field(ge=1)
    differs: list[_Diff] = Field(max_length=lim.MAX_EQUIV_DIFFS)


class _TraceRow(_Strict):
    cycle: int = Field(ge=1)
    inputs: _Named = Field(default_factory=dict, max_length=8)
    rtl: _Named = Field(default_factory=dict, max_length=8)
    netlist: _Named = Field(default_factory=dict, max_length=8)


class _Stage1(_Strict):
    status: str = Field(max_length=20)
    proven: int = Field(default=0, ge=0)
    unproven: int = Field(default=0, ge=0)
    unproven_signals: list[str] = Field(default_factory=list, max_length=lim.MAX_EQUIV_DIFFS)
    unpaired_outputs: list[str] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _strings_are_short(self) -> _Stage1:
        if any(
            len(s) > lim.EQUIV_SIGNAL_MAX for s in self.unproven_signals + self.unpaired_outputs
        ):
            raise ValueError("a signal name is too long")
        return self


class _Base(_Strict):
    status: str = Field(max_length=20)
    run: str | None = Field(default=None, max_length=10)
    cycle: int | None = Field(default=None, ge=1)
    differs: list[_Diff] = Field(default_factory=list, max_length=lim.MAX_EQUIV_DIFFS)


class _Equiv(_Strict):
    """The `equiv` verb's verdict, as the host accepts it (see `worker/equiv.py`)."""

    verdict: Verdict
    ok: bool
    reason: str | None = Field(default=None, max_length=40)
    method: str | None = Field(default=None, max_length=60)
    message: str = Field(max_length=lim.MESSAGE_MAX)
    seconds: dict[Annotated[str, Field(max_length=20)], float] = Field(
        default_factory=dict, max_length=8
    )
    stage1: _Stage1 | None = None
    base: _Base | None = None
    first_mismatch: _Mismatch | None = None
    trace: list[_TraceRow] = Field(default_factory=list, max_length=lim.MAX_EQUIV_TRACE)
    internal_inconsistency: bool = False
    cycles_covered: int | None = Field(default=None, ge=0)
    time_limit_hit: bool = False
    detail: list[str] = Field(default_factory=list, max_length=lim.MAX_EQUIV_DETAIL)
    netlist_sha256: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _consistent(self) -> _Equiv:
        # Only `equivalent` is ok, and an equivalent verdict has no reason, a method and no
        # mismatch: a verdict that contradicts itself is never believed.
        if self.ok != (self.verdict == "equivalent"):
            raise ValueError("ok does not match the verdict")
        if self.verdict == "equivalent" and (
            self.reason is not None
            or self.method is None
            or self.first_mismatch is not None
            or self.netlist_sha256 is None
        ):
            raise ValueError("an equivalent verdict contradicts itself")
        if self.verdict == "equivalent":
            if self.method == "equiv_induct":
                s1, base = self.stage1, self.base
                if (
                    s1 is None
                    or s1.status != "proven"
                    or s1.unproven != 0
                    or s1.unpaired_outputs
                    or base is None
                    or base.status != "ok"
                ):
                    raise ValueError("an induction proof that is not complete")
            elif not (self.method or "").startswith("k-induction length "):
                raise ValueError("an equivalent verdict names an unknown method")
        if self.verdict == "not_equivalent" and self.first_mismatch is None:
            raise ValueError("a not_equivalent verdict names no mismatch")
        if any(len(line) > lim.EQUIV_DETAIL_LINE_MAX for line in self.detail):
            raise ValueError("a detail line is too long")
        return self


class _EquivResult(EmitResult):
    """The `equiv` verb's result: the emit result, the synthesis part, and the verdict."""

    synth: _Synth | None = None
    equiv: _Equiv | None = None


# -- helpers -----------------------------------------------------------------------------------


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
    """Re-word a failed emit step for `check_equivalence` (the checks are `emit_verilog`'s)."""
    lines = emit_result.summary.splitlines()
    head = (
        "check_equivalence stopped before the check: the design did not pass the emit and lint "
        "step (the same checks as emit_verilog)."
    )
    rest = lines[1:] if lines and lines[0].startswith("emit_verilog failed") else lines
    hints = [h.replace("emit_verilog", "check_equivalence") for h in emit_result.next_hints]
    return ToolResult.from_lines(
        False,
        [head, *rest],
        details=emit_result.details,
        artifacts=emit_result.artifacts,
        next_hints=hints,
    )


def _synthesis_stopped(
    frontend: Frontend, project: Project, spec: Spec, config: TTConfig, synth: _Synth
) -> ToolResult:
    """Synthesis ended the call before there was a netlist to compare."""
    if synth.state == "too_big":
        sub = _early_stop(frontend, project, spec, config, synth)
    elif synth.state == "timeout":
        sub = _failure(
            frontend,
            project,
            ["Synthesis ran out of time: " + (synth.message or "Yosys did not finish") + "."],
            stage="timeout",
            hints=["Make the design smaller and call synthesize again."],
            timed_out=True,
        )
    else:
        sub = _yosys_failure(frontend, project, synth)
    lines = [
        "Equivalence was not checked: synthesis failed, so there is no netlist to compare.",
        *sub.summary.splitlines(),
    ]
    details = {**sub.details, "stage": sub.details.get("stage", "synth_failed")}
    hints = list(sub.next_hints) or [
        "Call synthesize to see why synthesis fails, then call check_equivalence again."
    ]
    return ToolResult.from_lines(False, lines, details=details, next_hints=hints)


def _synthesis_problems(
    frontend: Frontend, project: Project, synth: _Synth, problems: list[str]
) -> ToolResult:
    lines = [
        "Equivalence was not checked: synthesis found a problem in the gates.",
        *problems[:SUMMARY_FINDINGS],
    ]
    if len(problems) > SUMMARY_FINDINGS:
        lines.append(f"... {len(problems) - SUMMARY_FINDINGS} more in details.checks")
    return _failure(
        frontend,
        project,
        lines,
        stage="synth_failed",
        hints=[
            "Fix the first problem (file and line above) with write_rtl, then call "
            "check_equivalence again. synthesize shows the same findings."
        ],
        checks=[c.model_dump() for c in synth.checks],
        forbidden_cells=list(synth.forbidden_cells),
    )


def _trace_lines(equiv: _Equiv) -> list[str]:
    """The last cycles up to the mismatch: the inputs, then the output bits that differ."""
    if equiv.first_mismatch is None:
        return []
    # A row names a whole port (`uo_out`) or, for a power-up difference, single bits (`uo_out[3]`).
    bad = {d.signal for d in equiv.first_mismatch.differs}
    bad |= {name.split("[", 1)[0] for name in bad}
    lines: list[str] = []
    for row in equiv.trace[-SUMMARY_TRACE_ROWS:]:
        ins = " ".join(f"{k}={show_bits(v)}" for k, v in row.inputs.items())
        outs = [
            f"{k} RTL {show_bits(row.rtl.get(k))} netlist {show_bits(row.netlist.get(k))}"
            for k in sorted(row.rtl)
            if k in bad
        ]
        lines.append(f"  cycle {row.cycle}: {ins}" + (f" -> {'; '.join(outs)}" if outs else ""))
    return lines


def _method_text(method: str | None) -> str:
    if method and method.startswith("k-induction"):
        return f"{method} from reset, on the pins"
    return "induction over every register, plus a base case for every power-up state"


NOT_CHECKED = (
    "Not checked: bugs in the RTL itself (a wrong or missing reset in the RTL is the same on both "
    "sides) and timing. Gate-level simulation (run_tests with gl=True) stays the check for reset "
    "and power-up behaviour."
)

_GUARD_HINTS = {
    "guard_clocking": (
        "Use one clock (clk, rising edge) and no latches: a falling-edge flip-flop, a clock made "
        "inside the design and an always_latch cannot be proven. Run gate-level simulation "
        "(run_tests with gl=True) for such a design instead."
    ),
    "guard_tristate": (
        "Drive uio_out and set uio_oe instead of writing z, then call check_equivalence again."
    ),
    "guard_async_set_reset": (
        "Use a flip-flop with one reset only (the synchronous rst_n of the template), then call "
        "check_equivalence again. Gate-level simulation (run_tests with gl=True) still checks it."
    ),
    "guard_sim_synth_differ": (
        "Remove the construct the message names (a macro, a delay, a type such as `bit`, "
        "`unique`/`priority`, `casex`, an x or z literal, ...): a simulator and the synthesis "
        "read it differently. Then call check_equivalence again."
    ),
    "guard_include": (
        "Put the code of an `include file into the design file (src/<name>.v), then call "
        "check_equivalence again."
    ),
    "time_limit": (
        "The proof ran out of time. Make the design smaller or simpler (wide multipliers and big "
        "memories are the hard cases), or rely on gate-level simulation (run_tests with gl=True)."
    ),
    "unproven_points": (
        "Not proven is not wrong: the solver could not decide these points. Make the logic "
        "simpler, keep register names the same, or rely on gate-level simulation "
        "(run_tests with gl=True)."
    ),
    "power_up_dependent": (
        "Reset every register that feeds an output with rst_n, then call check_equivalence again."
    ),
    "base_case": (
        "Reset every register that feeds an output with rst_n, then call check_equivalence again."
    ),
    "initial_values": (
        "Reset those registers with rst_n instead of giving them an initial value (`reg x = 0`, "
        "`initial`, Amaranth reset_less or init=...), then call check_equivalence again."
    ),
    "state_check_unusable": (
        "Reset every register with rst_n and remove the initial values (`reg x = 0`, `initial`, "
        "Amaranth init=...), then call check_equivalence again."
    ),
    "unreadable_counterexample": (
        "The solver found a difference it could not describe. Run run_tests with "
        "simulator='icarus' and gl=True to look for it in simulation, and call synthesize."
    ),
}


def _first_sentence_free(message: str) -> str:
    """A not-proven message without the 'Could not prove equivalence:' lead-in."""
    for lead in ("Could not prove equivalence: ", "could not prove equivalence: "):
        if message.startswith(lead):
            message = message[len(lead) :]
    return message[:1].upper() + message[1:]


def _verdict_result(
    project: Project,
    frontend: Frontend,
    spec: Spec,
    config: TTConfig,
    synth: _Synth,
    equiv: _Equiv,
    sha: str,
    emit_result: ToolResult,
) -> ToolResult:
    assert synth.stat is not None
    top = f"tt_um_{project.name}"
    util = estimate_utilization(synth.stat.area_um2, spec.tiles, config)
    warnings = list(clock_warnings(spec.clock_hz))
    if util.verdict == "too_big":
        warnings.append(
            f"the gates would not fit a {spec.tiles} tile (synthesize reports it as too big); "
            "this run only checks that they match the RTL."
        )
    facts: dict[str, Any] = {
        "verdict": equiv.verdict,
        "reason": equiv.reason,
        "method": equiv.method,
        "netlist_sha256": sha,
    }
    details: dict[str, Any] = {
        "stage": "equiv_error" if equiv.verdict == "error" else equiv.verdict,
        "frontend": frontend.key,
        "top": top,
        "verdict": equiv.verdict,
        "reason": equiv.reason,
        "method": equiv.method,
        "message": equiv.message,
        "netlist_sha256": sha,
        "equiv": facts,
        "stage1": equiv.stage1.model_dump() if equiv.stage1 else None,
        "base": equiv.base.model_dump() if equiv.base else None,
        "first_mismatch": equiv.first_mismatch.model_dump() if equiv.first_mismatch else None,
        "trace": [row.model_dump() for row in equiv.trace],
        "internal_inconsistency": equiv.internal_inconsistency,
        "cycles_covered": equiv.cycles_covered,
        "time_limit_hit": equiv.time_limit_hit,
        "seconds": equiv.seconds,
        "synth": {
            "cells": synth.stat.cells,
            "flip_flops": synth.stat.flip_flops,
            "area_um2": round(synth.stat.area_um2, 3),
            "netlist_sha256": sha,
        },
        "warnings": warnings,
        "limits": {"per_step_s": BUDGET_S, "total_s": EQUIV_DEADLINE_S},
    }
    seconds = equiv.seconds.get("total", 0.0)
    notes = [f"Warning: {w}" for w in warnings[:2]]
    if equiv.verdict == "equivalent":
        lines = [
            "Equivalent: the sky130 netlist does the same as the RTL "
            f"(proven by {_method_text(equiv.method)}).",
            f"{equiv.message} The check took {seconds:.0f} s.",
            NOT_CHECKED,
            *notes,
            f"Netlist checked: sha256 {sha[:12]}... (the one synthesis just wrote).",
        ]
        hints = ["Call status to see the checks that are still open."]
        return ToolResult.from_lines(
            True,
            lines,
            details=details,
            artifacts=list(emit_result.artifacts),
            next_hints=hints,
        )
    if equiv.verdict == "not_equivalent":
        lines = [f"NOT equivalent: {equiv.message}"]
        if equiv.internal_inconsistency:
            lines.append(
                "The proof and the check from reset disagreed: treat this as a difference."
            )
        if equiv.trace:
            lines.append("What happened up to there (cycle 1 is power-up with rst_n low):")
            lines += _trace_lines(equiv)
        lines += notes
        return ToolResult.from_lines(
            False, lines, details=details, next_hints=_not_equivalent_hints(equiv)
        )
    if equiv.verdict == "not_proven":
        why = _first_sentence_free(equiv.message)
        if NOT_A_PASS not in why:  # whatever the worker said, never leave it out
            why = f"{why} {NOT_A_PASS}"
        lines = [f"Not proven: {why}"]
        if equiv.cycles_covered:
            lines.append(
                f"No difference was found in the first {equiv.cycles_covered} cycles from reset."
            )
        if equiv.time_limit_hit:
            lines.append(
                f"A step was stopped at its time limit ({BUDGET_S:.0f} s per step, "
                f"{EQUIV_DEADLINE_S:.0f} s in all)."
            )
        lines += [*notes, "Status shows Equivalence as fail until it is proven."]
        hints = [_GUARD_HINTS.get(equiv.reason or "", _GUARD_HINTS["unproven_points"])]
        return ToolResult.from_lines(False, lines, details=details, next_hints=hints)
    lines = [f"Equivalence check failed: {equiv.message}", *equiv.detail[:3]]
    return ToolResult.from_lines(
        False,
        lines,
        details=details,
        next_hints=[
            "Call synthesize to see whether the gates themselves have a problem; if it is ok, "
            "call toolchain_info and check that the toolchain image matches this version."
        ],
    )


def _not_equivalent_hints(equiv: _Equiv) -> list[str]:
    assert equiv.first_mismatch is not None
    mm = equiv.first_mismatch
    names = ", ".join(d.signal for d in mm.differs[:3])
    if equiv.reason == "rtl_initial_value":
        return [
            f"Reset {names} (and any register like it) with rst_n instead of an initial value "
            "(`reg x = 0`, `initial`, Amaranth reset_less or init=...), then call "
            "check_equivalence again."
        ]
    hints = [
        f"Look at {names} at cycle {mm.cycle}. Run run_tests with simulator='icarus' and gl=True "
        "to watch the gates against your tests (inspect_waves reads the gate-level waves), and "
        "call synthesize to read its warnings."
    ]
    if any(d.netlist in (None, "x") or "x" in (d.netlist or "") for d in mm.differs):
        hints.append(
            "The netlist value is x: it depends on the power-up state. A register that rst_n "
            "does not set drives that output; reset it, then call check_equivalence again."
        )
    return hints


def _handle(
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
                f"The equivalence check ran out of time: the container call did not finish in "
                f"{EQUIV_TIMEOUT_S:.0f} s and was stopped.",
                "This is NOT a pass. The design is probably too big or too complicated.",
            ],
            stage="timeout",
            hints=[
                "Make the design smaller (fewer bits, no wide multipliers or large arrays), or "
                "rely on gate-level simulation (run_tests with gl=True)."
            ],
            timed_out=True,
        )
    emit_report = handle_outcome(ctx, project, frontend, author, outcome)
    if not emit_report.ok:
        return _from_emit_failure(emit_report)
    assert outcome.result is not None
    try:
        result = _EquivResult.model_validate(outcome.result)
    except ValidationError:
        return _unreadable(frontend, project, outcome, "the equivalence result has the wrong shape")
    synth = result.synth
    if synth is None:
        return _unreadable(frontend, project, outcome, "the result has no synthesis part")
    if synth.state in ("yosys_failed", "timeout", "too_big"):
        if synth.state == "too_big" and synth.estimate is None:
            return _unreadable(frontend, project, outcome, "the result has no size estimate")
        return _synthesis_stopped(frontend, project, spec, config, synth)
    if synth.state != "ran" or synth.stat is None or synth.netlist is None:
        return _unreadable(frontend, project, outcome, "the synthesis part is incomplete")
    problems = [
        f"The netlist uses {_plain_cell(cell)}, a cell Tiny Tapeout's flow does not allow."
        for cell in synth.forbidden_cells
    ] + [f"{_location(c)}: {c.kind.replace('_', ' ')}: {c.message}" for c in synth.checks]
    if problems:
        return _synthesis_problems(frontend, project, synth, problems)
    equiv = result.equiv
    if equiv is None:
        return _unreadable(frontend, project, outcome, "the result has no verdict")
    # The netlist is still in the run folder: hash the bytes, and insist that the worker checked
    # the same file it synthesized (the verdict is a fact about exactly these bytes).
    files = _read_synth_files(project, outcome, synth, f"tt_um_{project.name}")
    if isinstance(files, str):
        return _unreadable(frontend, project, outcome, files)
    sha = hashlib.sha256(files[0]).hexdigest()
    if synth.netlist_sha256 != sha or (equiv.netlist_sha256 not in (None, sha)):
        return _unreadable(
            frontend, project, outcome, "the netlist changed while it was being checked"
        )
    if equiv.verdict == "equivalent" and equiv.netlist_sha256 != sha:
        return _unreadable(
            frontend, project, outcome, "the verdict does not say which netlist it checked"
        )
    return _verdict_result(project, frontend, spec, config, synth, equiv, sha, emit_report)


def check_equivalence(ctx: ServerContext, project_name: str) -> ToolResult:
    project = get_project(ctx, project_name)
    frontend = get_frontend(project.frontend)
    with project_lock(project.dir):
        # The synthesis reads src/config.json and info.yaml, so they are brought in step with
        # spec.yaml first and the digest below covers them (a ToolError if the spec is unusable).
        spec = sync_for_synthesis(project)
        config = read_tt_config(project.dir)
        before = snapshot_digests(project, ctx.image)
        result = _locked(ctx, project, frontend, spec, config)
        return record_run(project, ctx.image, "check_equivalence", result, before)


def _locked(
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
        "timeout_s": SYNTH_TIMEOUT_S,  # the synthesis part of the call
        "equiv_deadline_s": EQUIV_DEADLINE_S,
    }
    try:
        try:
            outcome = ctx.get_runner().run(
                "equiv", request, project_dir=project.dir, timeout_s=EQUIV_TIMEOUT_S
            )
        except ToolchainError as exc:
            raise ToolError(str(exc)) from exc
        return _handle(ctx, project, frontend, author, spec, config, outcome)
    finally:
        remove_run_dirs(project.dir / "build")


DESCRIPTION = """\
Check that the real chip gates do the same thing as your RTL. The tool runs the same checks as \
emit_verilog, synthesizes the design to sky130 gates exactly as synthesize does (a problem \
there stops it), and then a proof program (Yosys) compares the gates with the RTL. The first \
line of the summary is one of: "Equivalent: ..." (ok is true: proven), "NOT equivalent: ..." \
(a real difference: it names the output bit, the clock cycle, the RTL and netlist values and a \
short trace, where cycle 1 is power-up with rst_n low) or "Not proven: ... This is NOT a pass." \
(the tool could not decide: ok is false and Status shows Equivalence as fail). Not proven is \
different from not equivalent: it says why, for example latches, a falling-edge or derived \
clock, tri-state logic (z) and flip-flops with both an asynchronous set and reset are not \
supported, or the proof ran out of time. What is proven: the gates implement the RTL as \
Yosys reads it, with comments, attributes and \
macros out of the way (synthesis did not change your logic). A simulator can read some \
constructs differently from Yosys. The tool refuses the common ones as not proven and names \
them: macros and `include, bit/int types, unique/priority, casex, ===, x or z literals, delays \
(#), characters outside plain ASCII, a lint_off of a race warning, every level-sensitive \
always @(a or b) in Verilog or Amaranth (write always @(*)), a MyHDL @always(level signal) or \
@instance that waits on a level (use @always_comb), and a function without automatic. It does \
NOT detect an out-of-range index or a division by zero (x in a simulator, some value in the \
gates) or anything that depends on simulation event order: run gate-level simulation \
(run_tests with gl=True) as well.\n\
What it checks: every output value the RTL defines must match for every power-up state of \
the chip's flip-flops (an x or default-don't-care in the RTL means any value is accepted); \
registers that nothing resets are only compared for a consistent power-up (the RTL simulation \
shows x there); an RTL register that keeps an initial value (reg x = 0, initial blocks, Amaranth \
reset_less or init) which reset does not set is reported, as not equivalent when it shows on a pin \
within 32 cycles of reset and otherwise as not proven (never a pass), with the message to reset \
those registers with rst_n; a gate register that reset never sets while the RTL defines the \
value is caught. What it does NOT check: bugs in the RTL itself (a wrong or missing reset is \
the same on both sides, so it is not found), reset held longer than one cycle, and timing. \
Gate-level simulation (run_tests with gl=True) stays the check for reset and power-up \
behaviour, and the tests you wrote are still what says the design does the right thing: \
equivalence only says the gates match the RTL.\n\
It uses time limits of 120 s per proof step and 300 s for the check, on top of the synthesis \
time (up to 900 s). Typical time: seconds for a small design (a counter takes about 10 s), a \
minute or more for memories and wide multipliers, which can end as not proven. The netlist is \
not saved (use synthesize for that); the ledger records the verdict and the netlist hash.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="check_equivalence",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    async def check_equivalence_tool(project: str) -> ToolResult:
        return await asyncio.to_thread(check_equivalence, ctx, project)
