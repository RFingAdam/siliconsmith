# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`run_tests`: simulate the design with the project's cocotb tests on Icarus or Verilator.

With `gl=True` the same tests run on the synthesized sky130 netlist instead of the RTL (gate-level
simulation, Icarus only). The worker's `test` verb then does the whole job in one container call:
emit, the shared synthesis (`worker/synth.py`), Icarus on the netlist and the sky130 cell models.
The netlist stays in the run folder (and is removed with it); `build/synth` belongs to
`synthesize`. A gate-level run has its own build, sim and wave folders (`icarus-gl`), so it never
disturbs an RTL result, and the ledger marks it (`RunRecord.gl`) so it never counts for the RTL
simulation or coverage steps.

One container call (the worker's `test` verb) re-runs the whole emit pipeline and, only if that
is ok, builds and runs the tests. The emit part is handled by the same code as `emit_verilog`
(`handle_outcome`), so a design that does not elaborate or lint gives the same report and the
same files in `src/`. The simulation part comes back as untrusted data and is validated with
pydantic before anything is shown.

The result's `ok` is true only if the emit step was ok, at least one test passed (skipped tests
do not count) and none failed or errored.

`details` shape (stable; the validation ledger records from it, see `ledger.record_from_result`):

    sim            "icarus" | "verilator"
    gl             null for an RTL run; for a gate-level run {defines, netlist_sha256, synth}:
                   `defines` is what the netlist build defined (FUNCTIONAL, SIM, UNIT_DELAY),
                   `netlist_sha256` the hash of the netlist that was simulated (null when
                   synthesis stopped the run) and `synth` the synthesis facts (cells, flip_flops,
                   area_um2, utilization, tiles, verdict), or null
    stage          last stage reached: elaborate | ports | lint | synth_failed | too_big |
                   timeout | build | sim (the synthesis stages only in a gate-level run)
    filtered       true if a test_filter was given (such a run never counts as a full run)
    test_filter    the regex, or null
    lint           the emit step's lint findings (empty when the emit step was ok)
    tests          [{name, module, outcome pass|fail|skip|error, covers[], declared_covers[],
                     expects_failure, sim_time_ns, message, file, line, assertion, expected,
                     actual}]; `covers` is empty for an expect_fail/expect_error test (it
                     covers nothing), `declared_covers` keeps the ids its docstring names
    tests_total    number of tests reported
    waves          project-relative wave file (build/waves/icarus-gl/... for a gate-level run),
                   or null
    coverage       {covered, uncovered, unknown} against spec.yaml, or null without a valid spec
    log            simulator error lines for a failed build or a crashed run
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from siliconsmith.context import ServerContext
from siliconsmith.covers import CocotbTest, coverage, parse_covers
from siliconsmith.frontends import Frontend, get_frontend
from siliconsmith.ledger import record_run, snapshot_digests
from siliconsmith.paths import MissingFileError, project_lock, safe_read
from siliconsmith.project import Project
from siliconsmith.result import Artifact, ToolResult, make_summary
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.docker import ToolchainError, remove_run_dirs
from siliconsmith.spec import Spec
from siliconsmith.tools._common import MAX_WRITE_BYTES, get_project, read_spec_ids_locked
from siliconsmith.tools.emit_verilog import (
    EmitResult,
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
from siliconsmith.tt_sync import clock_period_ns, sync_for_synthesis
from siliconsmith.utilization import (
    TTConfig,
    estimate_utilization,
    pre_abc_budget_um2,
    read_tt_config,
)
from siliconsmith.worker import limits as lim

SIM_TIMEOUT_S = 600.0
MAX_FILTER_CHARS = 200
SUMMARY_NAMES = 6
WAVES_RE = re.compile(r"^build/waves/(icarus|verilator)/tb\.(fst|vcd)$")
GL_WAVES_RE = re.compile(r"^build/waves/icarus-gl/tb\.(fst|vcd)$")
GL_LABEL = "Gate-level simulation (sky130 netlist, icarus)"
MODULE_RE = re.compile(r"^test[A-Za-z0-9_]*$")

Simulator = Literal["icarus", "verilator"]
Outcome = Literal["pass", "fail", "skip", "error"]


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Test(_Strict):
    name: str = Field(max_length=lim.TEST_NAME_MAX)
    module: str = Field(default="", max_length=lim.TEST_NAME_MAX)
    outcome: Outcome
    sim_time_ns: float | None = Field(default=None, ge=0)
    message: str | None = Field(default=None, max_length=lim.TEST_TEXT_MAX)
    file: str | None = Field(default=None, max_length=lim.TEST_NAME_MAX)
    line: int | None = Field(default=None, ge=1)
    assertion: str | None = Field(default=None, max_length=lim.TEST_TEXT_MAX)
    expected: str | None = Field(default=None, max_length=lim.TEST_VALUE_MAX)
    actual: str | None = Field(default=None, max_length=lim.TEST_VALUE_MAX)


class _Sim(_Strict):
    state: Literal["ran", "build_failed", "no_results", "no_testbench", "no_modules"]
    simulator: Simulator
    tests: list[_Test] = Field(default_factory=list, max_length=lim.MAX_TESTS)
    tests_total: int = Field(default=0, ge=0)
    waves: str | None = Field(default=None, max_length=lim.WAVES_PATH_MAX)
    log: list[Annotated[str, Field(max_length=lim.LOG_LINE_MAX)]] = Field(
        default_factory=list, max_length=lim.MAX_LOG_LINES
    )
    exit_code: int | None = None
    message: str | None = Field(default=None, max_length=lim.TEST_TEXT_MAX)


class _DiffPin(_Strict):
    signal: str = Field(max_length=lim.TEST_NAME_MAX)
    rtl: str = Field(max_length=lim.TEST_VALUE_MAX)
    gl: str = Field(max_length=lim.TEST_VALUE_MAX)


class _Diff(_Strict):
    """The worker's RTL-versus-gate-level first difference (worker/gldiff.py)."""

    state: Literal[
        "differs", "same", "no_clock", "no_edge", "no_pins", "too_big", "skipped", "unavailable"
    ]
    reason: str | None = Field(default=None, max_length=lim.TEST_TEXT_MAX)
    cycle: int | None = Field(default=None, ge=1)
    time_ns: float | None = Field(default=None, ge=0)
    cycles_compared: int = Field(default=0, ge=0)
    signals: list[_DiffPin] = Field(default_factory=list, max_length=8)
    rtl_failed: bool = False
    edges_skipped: int = Field(default=0, ge=0)
    gl_known_before: bool = False
    input_unknown: str | None = Field(default=None, max_length=lim.TEST_NAME_MAX)
    reset_never_low: bool = False
    reset_low: bool = False
    truncated: bool = False
    cycle_limit: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _differs_has_a_place(self) -> _Diff:
        if self.state == "differs" and not (
            self.signals and self.cycle is not None and self.time_ns is not None
        ):
            raise ValueError("a difference needs signals, a cycle and a time")
        return self


class _Gl(_Strict):
    """The worker's `gl` entry: the netlist build's defines and the synthesis outcome."""

    defines: dict[Annotated[str, Field(max_length=64)], Annotated[str, Field(max_length=64)]] = (
        Field(default_factory=dict, max_length=16)
    )
    synth: _Synth
    netlist_sha256: str | None = Field(default=None, max_length=64)
    simulated: bool = False
    seed: int | None = Field(default=None, ge=0, le=2**64)
    diff: _Diff | None = None


class _RunResult(EmitResult):
    sim: _Sim | None = None
    gl: _Gl | None = None


# -- helpers ---------------------------------------------------------------------------------


def _check_filter(test_filter: str | None) -> str | None:
    if test_filter is None or not test_filter.strip():
        return None
    if len(test_filter) > MAX_FILTER_CHARS:
        raise ToolError(f"test_filter is longer than {MAX_FILTER_CHARS} characters")
    try:
        re.compile(test_filter)
    except re.error as exc:
        raise ToolError(
            f"test_filter {test_filter!r} is not a valid regular expression ({exc}); it is "
            "searched in '<module>.<test name>', for example 'wrap' or 'test_count.*'"
        ) from exc
    return test_filter


def _retitle(result: ToolResult, details: dict[str, Any]) -> ToolResult:
    """An emit-stage failure, reported as a run_tests result (no test ran)."""
    lines = result.summary.splitlines()
    if lines:
        lines[0] = lines[0].replace(
            "emit_verilog failed", "run_tests did not simulate: the emit step failed", 1
        )
    result.summary = make_summary(lines)
    result.next_hints = [h.replace("emit_verilog", "run_tests") for h in result.next_hints]
    result.details = {**details, **result.details}
    return result


def _gl_details(
    defines: dict[str, str] | None = None,
    netlist_sha256: str | None = None,
    synth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "defines": defines or {},
        "netlist_sha256": netlist_sha256,
        "synth": synth,
        "diff": None,
    }


def _is_gl(details: dict[str, Any]) -> bool:
    return details.get("gl") is not None


def _base_details(simulator: str, test_filter: str | None, gl: bool = False) -> dict[str, Any]:
    return {
        "sim": simulator,
        "gl": _gl_details() if gl else None,
        "filtered": test_filter is not None,
        "test_filter": test_filter,
        "lint": [],
        "tests": [],
        "tests_total": 0,
        "waves": None,
        "coverage": None,
        "log": [],
    }


def _read_covers(project: Project, modules: list[str]) -> dict[tuple[str, str], CocotbTest]:
    """Static `Covers:` info for each test, from `test/<module>.py` (parsed, never run)."""
    found: dict[tuple[str, str], CocotbTest] = {}
    for module in dict.fromkeys(modules):
        if not MODULE_RE.fullmatch(module):
            continue
        try:
            raw = safe_read(project.dir, f"test/{module}.py", MAX_WRITE_BYTES)
        except (MissingFileError, ToolError):
            continue
        report = parse_covers(raw.decode("utf-8", errors="replace"), f"test/{module}.py")
        for test in report.tests:
            found[(module, test.name)] = test
    return found


def _count(tests: list[dict[str, Any]], outcome: str) -> int:
    return sum(1 for t in tests if t["outcome"] == outcome)


def _failure_lines(first: dict[str, Any]) -> list[str]:
    where = f" ({first['file']}:{first['line']})" if first["file"] and first["line"] else ""
    lines = [f"First failure: {first['name']}{where}"]
    if first["assertion"]:
        lines.append(f"  assertion: {first['assertion']}")
    if first["expected"] is not None and first["actual"] is not None:
        lines.append(f"  expected: {first['expected']}")
        lines.append(f"  actual:   {first['actual']}")
    if first["message"]:
        lines.append(f"  message: {first['message']}")
    return lines


def _pin_values(diff: dict[str, Any]) -> str:
    shown = diff["signals"][0]
    text = (
        f"{shown['signal']} at clock cycle {diff['cycle']} "
        f"(counted from the start of the first test; t={diff['time_ns']:g} ns)"
    )
    return f"{text}: RTL {shown['rtl']}, gate level {shown['gl']}"


def _same_lines(diff: dict[str, Any]) -> list[str]:
    if diff.get("truncated"):
        limit = diff.get("cycle_limit") or diff["cycles_compared"]
        lines = [
            f"The pins match the RTL for the first {limit:,} clock cycles; the failure came "
            "later and was not compared."
        ]
    elif diff.get("rtl_failed"):
        return [
            "The pins match the RTL, and the same test fails on the RTL too: the problem is in "
            "the design or the test, not in the gates."
        ]
    else:
        edges = "every clock edge after reset" if diff.get("edges_skipped") else "every clock edge"
        return [
            f"The pins uo_out, uio_out and uio_oe match the RTL at {edges} until the failure "
            f"({diff['cycles_compared']} cycles compared), so the failing check most likely "
            "reads something that is not a pin (an internal signal is not in the netlist) or "
            "depends on timing."
        ]
    if diff.get("rtl_failed"):
        lines.append(
            "The same test also fails on the RTL, so look at the design or the test first."
        )
    return lines


def _no_edge_reason(diff: dict[str, Any]) -> str:
    if diff.get("reset_low"):
        return (
            "rst_n stayed low until the failure: hold it low for a few clock cycles, then set "
            "it to 1"
        )
    return (
        "the failure came before reset was released and the first clock edge after it, so "
        "there is nothing to compare yet"
    )


def _diff_lines(diff: dict[str, Any]) -> list[str]:
    """One or two plain lines for the RTL-versus-gate-level comparison of a failing GL run."""
    state = diff["state"]
    if state == "differs":
        lines = [f"First difference on the pins: {_pin_values(diff)}."]
        others = [p["signal"] for p in diff["signals"][1:]]
        if others:
            lines[0] = lines[0][:-1] + f" ({', '.join(others)} differ at the same cycle)."
        shown = diff["signals"][0]
        if "x" in shown["gl"] and "x" not in shown["rtl"]:
            if diff.get("input_unknown"):
                lines.append(
                    f"{diff['input_unknown']} was never driven (x): the RTL treats an unknown "
                    "`if` as false, the gates do not. Drive every input (ui_in, uio_in, ena) "
                    "in the test."
                )
            elif diff.get("reset_never_low"):
                lines.append(
                    "The test never held rst_n low, so the gates were never reset: hold rst_n "
                    "low for a few clock cycles, then set it to 1."
                )
            elif diff.get("gl_known_before"):
                lines.append(
                    "The gates turned unknown (x) at that cycle while the RTL has a value: a "
                    "register with no reset (or one that relies on an initial value) only now "
                    "reaches the pins. Use the reset."
                )
            else:
                lines.append(
                    "The gates start unknown (x) where the RTL starts at a value: some "
                    "flip-flops have no reset or rely on an initial value, which a real chip "
                    "ignores. Use the reset."
                )
        if diff.get("rtl_failed"):
            lines.append(
                "The same test also fails on the RTL, so look at the design or the test first."
            )
        return lines
    if state == "same":
        return _same_lines(diff)
    reason = diff.get("reason") or {
        "no_clock": "the waves have no tb.clk",
        "no_edge": _no_edge_reason(diff),
        "no_pins": "the waves have none of the chip's output pins",
        "too_big": "a wave file is too large to compare",
    }.get(state, "it could not be done")
    return [f"No RTL-versus-gate-level comparison: {reason}."]


def _name_list(tests: list[dict[str, Any]], outcome: str) -> str:
    names = [t["name"] for t in tests if t["outcome"] == outcome]
    shown = ", ".join(names[:SUMMARY_NAMES])
    return shown + (
        f", … (+{len(names) - SUMMARY_NAMES} more)" if len(names) > SUMMARY_NAMES else ""
    )


def _sim_problem(
    sim: _Sim, test_filter: str | None, details: dict[str, Any], frontend: Frontend, name: str
) -> ToolResult | None:
    """Results for a run that did not produce test outcomes (None if it did)."""
    if sim.state in ("no_testbench", "no_modules"):
        raise ToolError(
            (sim.message or "the project has no testbench or tests")
            + (
                "; write it with write_test (test/test.py is the starter test)"
                if sim.state == "no_modules"
                else "; create_project writes it and no tool can change it"
            )
        )
    editable = frontend.entry(name)
    gl = _is_gl(details)
    if sim.state == "build_failed":
        details["stage"] = "build"
        details["log"] = sim.log
        if gl:
            head = f"{GL_LABEL}: failed, icarus could not build the netlist and the testbench."
            hint = (
                "The netlist and the sky130 cell models come from the toolchain, so unless the "
                "error names one of your files under test/, call synthesize to check the "
                "netlist, then call run_tests with gl=True again."
            )
        else:
            head = f"run_tests failed: {sim.simulator} could not build the design and testbench."
            hint = (
                f"Fix the first error in {editable} (or in the test files); src/project.v and "
                "test/tb.v are generated or tool-owned. Then call run_tests again."
            )
        return ToolResult.from_lines(False, [head, *sim.log], details=details, next_hints=[hint])
    if sim.state == "no_results":
        details["stage"] = "sim"
        details["log"] = sim.log
        ended = f"{sim.simulator} ended without test results" + (
            f" (exit code {sim.exit_code})." if sim.exit_code is not None else "."
        )
        return ToolResult.from_lines(
            False,
            [
                f"{GL_LABEL}: failed, {ended}" if gl else f"run_tests failed: {ended}",
                sim.message or "",
                *sim.log,
            ],
            details=details,
            next_hints=[
                "Check the test files for import or syntax errors and for tests that crash the "
                "simulator, then call run_tests again."
            ],
        )
    return None


def _no_tests(sim: _Sim, test_filter: str | None, details: dict[str, Any]) -> ToolResult:
    if test_filter is not None:
        line = f"test_filter {test_filter!r} matched no test."
        hint = (
            "The filter is a regular expression searched in '<module>.<test name>' (for "
            "example 'test.test_wraps_at_255'), so '^' and '$' anchor that whole name; check "
            "the spelling."
        )
    else:
        line = "The test files contain no cocotb tests (@cocotb.test)."
        hint = (
            "Write tests with write_test: each is an async function decorated with @cocotb.test()."
        )
    head = (
        f"{GL_LABEL}: failed, no test ran."
        if _is_gl(details)
        else f"run_tests failed on {sim.simulator}: no test ran."
    )
    return ToolResult.from_lines(
        False,
        [head, line],
        details=details,
        next_hints=[hint],
    )


def _all_skipped(sim: _Sim, count: int, details: dict[str, Any]) -> ToolResult:
    return ToolResult.from_lines(
        False,
        [
            (
                f"{GL_LABEL}: failed, no test ran"
                if _is_gl(details)
                else f"run_tests failed on {sim.simulator}: no test ran"
            )
            + f": all {count} test{'s were' if count != 1 else ' was'} skipped."
        ],
        details=details,
        next_hints=[
            "Remove skip=True from the @cocotb.test() decorators (and any test_filter that "
            "leaves only skipped tests), then call run_tests again."
        ],
    )


def _finish(
    project: Project,
    frontend: Frontend,
    sim: _Sim,
    test_filter: str | None,
    emit_details: dict[str, Any],
    gl: dict[str, Any] | None = None,
) -> ToolResult:
    details = _base_details(sim.simulator, test_filter)
    details["gl"] = gl
    details["stage"] = "sim"
    details["lint"] = emit_details.get("lint", [])
    problem = _sim_problem(sim, test_filter, details, frontend, project.name)
    if problem is not None:
        return problem
    static = _read_covers(project, [t.module for t in sim.tests])
    tests: list[dict[str, Any]] = []
    for item in sim.tests:
        info = static.get((item.module, item.name))
        entry = item.model_dump()
        declared = list(info.covers) if info else []
        expects_failure = info.expects_failure if info else False
        # An expect_fail/expect_error test reports a failing check as a pass, so it covers
        # nothing; its ids stay visible as declared_covers.
        entry["covers"] = [] if expects_failure else declared
        entry["declared_covers"] = declared
        entry["expects_failure"] = expects_failure
        tests.append(entry)
    details["tests"] = tests
    details["tests_total"] = len(tests)
    pattern = GL_WAVES_RE if gl is not None else WAVES_RE
    waves = sim.waves if sim.waves and pattern.fullmatch(sim.waves) else None
    details["waves"] = waves
    spec_ids = read_spec_ids_locked(project)
    if spec_ids is not None and gl is None:  # gate-level tests never count for coverage
        details["coverage"] = coverage(spec_ids, tests).as_dict()
    if not tests:
        return _no_tests(sim, test_filter, details)

    passed, failed = _count(tests, "pass"), _count(tests, "fail") + _count(tests, "error")
    skipped = _count(tests, "skip")
    if passed == 0 and failed == 0:
        return _all_skipped(sim, len(tests), details)
    ok = failed == 0
    verdict = "ok" if ok else "failed"
    scope = f" (filter {test_filter!r})" if test_filter is not None else ""
    counts = f"{passed} passed, {failed} failed, {skipped} skipped ({len(tests)} tests)."
    if gl is not None:
        lines = [f"{GL_LABEL}: {verdict}{scope}, {counts}"]
    else:
        lines = [f"run_tests {verdict} on {sim.simulator}{scope}: {counts}"]
    hints: list[str] = []
    bad = [t for t in tests if t["outcome"] in ("fail", "error")]
    if bad:
        lines += _failure_lines(bad[0])
        if len(bad) > 1:
            lines.append(f"Other failing tests: {_name_list(bad[1:], bad[1]['outcome'])}")
        diff = gl.get("diff") if gl is not None else None
        if diff is not None:
            lines += _diff_lines(diff)
        if gl is not None:
            where = (
                f"Look at the gate-level pins with inspect_waves(path={waves!r}, "
                "signals=['uo_out', ...]). "
                if waves
                else "No gate-level wave file was kept for this run (see the note above). "
            )
            hints.append(
                where + "A gate-level test can only use the tb pins "
                "(dut.uo_out, dut.uio_out, dut.uio_oe, ...); inside the netlist the names "
                "look like \\core.count[0] or are gone. A value of x means unknown: a "
                "flip-flop that was never reset, or an input that was not driven yet."
            )
        elif waves:
            hints.append(
                f"Look at the failing signals with inspect_waves(path={waves!r}, "
                "signals=[...]) around the failure."
            )
        else:
            only = bad[0]["name"] + "$"  # identifiers, so nothing to escape
            if bad[0]["module"]:
                only = f"{bad[0]['module']}.{only}"
            hints.append(
                f"Rerun with waves=True and test_filter={only!r}, then use "
                "inspect_waves to look at the signals."
            )
    elif _count(tests, "pass"):
        lines.append(f"Passed: {_name_list(tests, 'pass')}")
    cov = details["coverage"]
    if test_filter is None and cov is not None:
        if cov["covered"]:
            lines.append(f"Requirements covered by passing tests: {', '.join(cov['covered'])}")
        if cov["uncovered"]:
            lines.append(f"Not covered yet: {', '.join(cov['uncovered'])}")
        if cov["unknown"]:
            lines.append(f"Tests name unknown requirements: {', '.join(cov['unknown'])}")
        if cov["uncovered"] and ok:
            hints.append(
                "Add tests with 'Covers: " + ", ".join(cov["uncovered"][:3]) + "' docstrings for "
                "the requirements that no passing test covers."
            )
    if waves:
        lines.append(f"Waves: {waves}")
    elif sim.message:
        lines.append(f"Note: {sim.message}")
    if ok and test_filter is not None:
        hints.append("This run was filtered; call run_tests without test_filter for a full run.")
    if ok and test_filter is None and gl is not None:
        hints.append("Call status to see the checks that are still open.")
    elif ok and test_filter is None:
        other = "verilator" if sim.simulator == "icarus" else "icarus"
        hints.append(f"Run the tests on {other} as well to check both simulators agree.")
    artifacts = [Artifact(path=waves, kind="waves")] if waves else []
    return ToolResult.from_lines(ok, lines, details=details, artifacts=artifacts, next_hints=hints)


def _timeout(
    simulator: str, test_filter: str | None, timeout_s: float, gl: bool = False
) -> ToolResult:
    details = _base_details(simulator, test_filter, gl)
    details["stage"] = "sim"
    details["timed_out"] = True
    if gl:
        return ToolResult.from_lines(
            False,
            [
                f"{GL_LABEL}: failed, the run did not finish in {timeout_s:.0f} s "
                "(synthesis and simulation together).",
                "It was stopped, so it is not known which of the two was slow; no test results "
                "were kept.",
            ],
            details=details,
            next_hints=[
                "Call synthesize to see whether synthesis alone finishes. If it does, a test "
                "may be waiting forever: give each test a timeout with "
                "@cocotb.test(timeout_time=100, timeout_unit='us'), and run one test at a time "
                "with test_filter."
            ],
        )
    return ToolResult.from_lines(
        False,
        [
            f"run_tests failed: simulation did not finish in {timeout_s:.0f} s "
            "(a test may be waiting forever).",
            "The simulator was stopped; no test results were kept.",
        ],
        details=details,
        next_hints=[
            "Look for a test that awaits an event that never comes (a missing clock, a Timer "
            "inside a loop with no end), or give each test a timeout: "
            "@cocotb.test(timeout_time=100, timeout_unit='us'). Run one test at a time with "
            "test_filter to find it."
        ],
    )


def run(
    ctx: ServerContext,
    project_name: str,
    simulator: Simulator,
    test_filter: str | None,
    waves: bool,
    gl: bool = False,
) -> ToolResult:
    if simulator not in ("icarus", "verilator"):
        raise ToolError(f"unknown simulator {simulator!r}; choose icarus or verilator")
    if gl and simulator != "icarus":
        raise ToolError(
            "gate-level simulation runs on icarus only: call run_tests with simulator='icarus' "
            "and gl=True (the cell models of the sky130 library are plain Verilog that Icarus "
            "reads directly)."
        )
    test_filter = _check_filter(test_filter)
    project = get_project(ctx, project_name)
    frontend = get_frontend(project.frontend)
    base = _base_details(simulator, test_filter, gl)
    with project_lock(project.dir):
        spec = None
        if gl:
            # The synthesis reads src/config.json and info.yaml, so they are brought in step
            # with spec.yaml first, and the digest below covers them (a ToolError if the spec
            # cannot be used).
            spec = sync_for_synthesis(project)
        before = snapshot_digests(project, ctx.image)
        result = _run_locked(ctx, project, frontend, simulator, test_filter, waves, base, spec)
        # Every result is a fact about these files (a ToolError is not, and is not recorded).
        return record_run(project, ctx.image, "run_tests", result, before)


def _run_locked(
    ctx: ServerContext,
    project: Project,
    frontend: Frontend,
    simulator: Simulator,
    test_filter: str | None,
    waves: bool,
    base: dict[str, Any],
    spec: Spec | None,
) -> ToolResult:
    author = read_author(project)
    early = precheck(project, frontend)
    if early is not None:
        base["stage"] = "elaborate"
        return _retitle(early, base)
    request: dict[str, Any] = {
        **frontend.worker_request(project.name, author),
        "simulator": simulator,
        "test_filter": test_filter,
        "waves": bool(waves),
        "gl": spec is not None,
    }
    timeout_s = SIM_TIMEOUT_S
    config: TTConfig | None = None
    if spec is not None:
        config = read_tt_config(project.dir)
        request["clock_period_ns"] = clock_period_ns(spec.clock_hz)
        request["stop_area_um2"] = pre_abc_budget_um2(spec.tiles, config)
        request["timeout_s"] = SYNTH_TIMEOUT_S  # the synthesis part of the call
        timeout_s = SYNTH_TIMEOUT_S + SIM_TIMEOUT_S
    try:
        try:
            outcome = ctx.get_runner().run(
                "test", request, project_dir=project.dir, timeout_s=timeout_s
            )
        except ToolchainError as exc:
            raise ToolError(str(exc)) from exc
        return _handle(
            ctx, project, frontend, author, outcome, simulator, test_filter, timeout_s, spec, config
        )
    finally:
        remove_run_dirs(project.dir / "build")


def _handle(
    ctx: ServerContext,
    project: Project,
    frontend: Frontend,
    author: str,
    outcome: WorkerOutcome,
    simulator: str,
    test_filter: str | None,
    timeout_s: float,
    spec: Spec | None = None,
    config: TTConfig | None = None,
) -> ToolResult:
    gl = spec is not None
    if outcome.timed_out:
        return _timeout(simulator, test_filter, timeout_s, gl)
    # The emit part (files into src/, the failure reports) is shared with emit_verilog.
    emit_report = handle_outcome(ctx, project, frontend, author, outcome)
    base = _base_details(simulator, test_filter, gl)
    if not emit_report.ok:
        base["stage"] = str(emit_report.details.get("stage", "elaborate"))
        return _retitle(emit_report, base)
    assert outcome.result is not None  # handle_outcome accepted it
    try:
        result = _RunResult.model_validate(outcome.result)
    except ValidationError:
        return _bad_sim_result(base, "the simulation part of the result has the wrong shape")
    gl_details: dict[str, Any] | None = None
    if spec is not None:
        assert config is not None
        if result.gl is None:
            return _bad_sim_result(base, "the result has no gate-level part")
        stopped = _gl_synthesis(project, frontend, outcome, result, spec, config, base)
        if isinstance(stopped, ToolResult):
            return stopped
        gl_details = stopped
    if result.sim is None or result.sim.simulator != simulator:
        return _bad_sim_result(base, "the result has no simulation part")
    if result.sim.tests_total < len(result.sim.tests):
        return _bad_sim_result(base, "the result contradicts itself")
    finished = _finish(project, frontend, result.sim, test_filter, emit_report.details, gl_details)
    if gl_details is not None and gl_details.get("warnings"):
        finished.summary = make_summary(
            [*finished.summary.splitlines(), *[f"Note: {w}" for w in gl_details.pop("warnings")]]
        )
        finished.details["gl"] = gl_details
    return finished


def _synth_facts(synth: _Synth, spec: Spec, config: TTConfig, sha: str | None) -> dict[str, Any]:
    """The synthesis facts a gate-level run keeps (the same fields as the `synthesize` record)."""
    facts: dict[str, Any] = {"tiles": spec.tiles, "netlist_sha256": sha}
    if synth.stat is not None:
        util = estimate_utilization(synth.stat.area_um2, spec.tiles, config)
        facts.update(
            cells=synth.stat.cells,
            flip_flops=synth.stat.flip_flops,
            area_um2=round(synth.stat.area_um2, 3),
            utilization=round(util.estimate, 4),
            verdict=util.verdict,
        )
    return facts


def _gl_synthesis(
    project: Project,
    frontend: Frontend,
    outcome: WorkerOutcome,
    result: _RunResult,
    spec: Spec,
    config: TTConfig,
    base: dict[str, Any],
) -> dict[str, Any] | ToolResult:
    """The synthesis half of a gate-level run: its facts when a netlist was simulated, or the
    result that ends the run (synthesis failed, too big, out of time, or a netlist problem)."""
    assert result.gl is not None
    synth = result.gl.synth
    if synth.state in ("yosys_failed", "timeout", "too_big"):
        return _gl_stopped(project, frontend, spec, config, synth, base)
    if synth.state != "ran" or synth.stat is None:
        return _bad_sim_result(base, "the synthesis part of the result is incomplete")
    sha: str | None = None
    if result.sim is not None:
        # The netlist is still in the run folder: hash the bytes the simulator was given.
        files = _read_synth_files(project, outcome, synth, f"tt_um_{project.name}")
        if isinstance(files, str):
            return _bad_sim_result(base, files)
        sha = hashlib.sha256(files[0]).hexdigest()
        if synth.netlist_sha256 is not None and synth.netlist_sha256 != sha:
            return _bad_sim_result(base, "the netlist changed while the tests ran")
    facts = _synth_facts(synth, spec, config, sha)
    gl = _gl_details(dict(result.gl.defines), sha, facts)
    gl["seed"] = result.gl.seed
    if result.gl.diff is not None:
        gl["diff"] = result.gl.diff.model_dump()
    problems = [
        f"The netlist uses {_plain_cell(cell)}, a cell Tiny Tapeout's flow does not allow."
        for cell in synth.forbidden_cells
    ] + [f"{_location(c)}: {c.kind.replace('_', ' ')}: {c.message}" for c in synth.checks]
    if result.sim is None and not problems:
        return _bad_sim_result(base, "the result has no simulation part")
    if problems:
        base["gl"] = gl
        base["stage"] = "synth_failed"
        lines = [
            f"{GL_LABEL} could not run: synthesis found a problem in the gates, so the netlist "
            "was not simulated.",
            *problems[:SUMMARY_NAMES],
        ]
        if len(problems) > SUMMARY_NAMES:
            lines.append(f"... {len(problems) - SUMMARY_NAMES} more in details.gl.checks")
        gl["checks"] = [c.model_dump() for c in synth.checks]
        gl["forbidden_cells"] = list(synth.forbidden_cells)
        return ToolResult.from_lines(
            False,
            lines,
            details=base,
            next_hints=[
                "Fix the first problem (file and line above) with write_rtl, then call "
                "synthesize and run_tests with gl=True again."
            ],
        )
    if estimate_utilization(synth.stat.area_um2, spec.tiles, config).verdict == "too_big":
        gl["warnings"] = [
            f"the gates would not fit a {spec.tiles} tile (synthesize reports it as too big); "
            "this run only checks how they behave."
        ]
    return gl


def _gl_stopped(
    project: Project,
    frontend: Frontend,
    spec: Spec,
    config: TTConfig,
    synth: _Synth,
    base: dict[str, Any],
) -> ToolResult:
    """A gate-level run that synthesis ended: its stage and findings, no simulation."""
    if synth.state == "too_big":
        sub = _early_stop(frontend, project, spec, config, synth)
    elif synth.state == "timeout":
        sub = ToolResult.from_lines(
            False,
            ["Synthesis ran out of time: " + (synth.message or "Yosys did not finish") + "."],
            details={"stage": "timeout", "timed_out": True},
            next_hints=["Make the design smaller and call synthesize again."],
        )
    else:
        sub = _yosys_failure(frontend, project, synth)
    reason = sub.summary.splitlines()
    lines = [
        f"{GL_LABEL} could not run: synthesis failed, so there is no netlist to simulate.",
        *reason,
    ]
    details = {**base, "stage": sub.details.get("stage", "synth_failed")}
    for key in ("log", "message", "estimate", "utilization", "yosys_seconds", "timed_out"):
        if key in sub.details:
            details[key] = sub.details[key]
    details["gl"] = _gl_details(synth=None)
    hints = list(sub.next_hints) or [
        "Call synthesize to see why synthesis fails, then call run_tests with gl=True again."
    ]
    return ToolResult.from_lines(False, lines, details=details, next_hints=hints)


def _bad_sim_result(details: dict[str, Any], why: str) -> ToolResult:
    details["stage"] = "sim"
    label = GL_LABEL if _is_gl(details) else "run_tests"
    return ToolResult.from_lines(
        False,
        [
            f"{label} failed: the simulation returned an unreadable result ({why}).",
            "No test results can be shown.",
        ],
        details=details,
        next_hints=[
            "Call toolchain_info to check that the toolchain image matches this version of "
            "Siliconsmith, then call run_tests again."
        ],
    )


DESCRIPTION = """\
Run the project's cocotb tests (every test/test*.py) against the design on Icarus Verilog or \
Verilator. It first does what emit_verilog does (elaborate, check ports, regenerate the wrapper, \
lint); only if that is ok does it simulate the Tiny Tapeout testbench test/tb.v. ok is true only \
if at least one test ran (skipped tests do not count) and none failed. The summary names the \
first failing test, its file:line, the assertion, and expected vs actual. test_filter is a regex \
that cocotb searches in '<module>.<test name>' (for example 'test.test_wraps_at_255$'); a \
filtered run never counts as a full run. waves=True keeps a wave file under build/waves/ for \
inspect_waves. details.tests lists every test with outcome and the requirement ids it covers; \
details.coverage says which spec.yaml requirements have a passing test. The first Verilator run \
takes about half a minute; later runs reuse the build.

gl=True runs the tests at gate level (Icarus only; with Verilator it is an error). The tool \
first does what synthesize does, with the same checks and the same time limits (a design that \
fails stops there, and so does one far over the size budget; the summary says gate-level \
simulation could not run. A design only slightly too big is simulated, with a note), then \
runs the SAME test files on the resulting sky130 netlist, with the cell models of the chip \
library (a netlist has no RTL names: a test may only use the pins of tb, such as uo_out, and \
not internal signals). This catches what RTL simulation cannot: logic that behaves differently \
once it is real gates, such as a missing reset or an uninitialized flip-flop. The summary's \
first line says "Gate-level simulation" so it is never mistaken for an RTL run. A gate-level \
run never changes the RTL results or the RTL wave files: its waves are kept apart in \
build/waves/icarus-gl/ (inspect_waves reads them). It can take up to 25 minutes in the worst \
case; a small design takes seconds to a minute. details.gl holds the netlist hash.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="run_tests",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True),
    )
    async def run_tests(
        project: str,
        simulator: Simulator,
        test_filter: str | None = None,
        waves: bool = False,
        gl: bool = False,
    ) -> ToolResult:
        return await asyncio.to_thread(run, ctx, project, simulator, test_filter, waves, gl)
