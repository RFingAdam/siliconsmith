# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`run_tests`: simulate the design with the project's cocotb tests on Icarus or Verilator.

One container call (the worker's `test` verb) re-runs the whole emit pipeline and, only if that
is ok, builds and runs the tests. The emit part is handled by the same code as `emit_verilog`
(`handle_outcome`), so a design that does not elaborate or lint gives the same report and the
same files in `src/`. The simulation part comes back as untrusted data and is validated with
pydantic before anything is shown.

`details` shape (stable; the ledger added in M0-8 records from it):

    sim            "icarus" | "verilator"
    stage          last stage reached: elaborate | ports | lint | build | sim
    filtered       true if a test_filter was given (such a run never counts as a full run)
    test_filter    the regex, or null
    lint           the emit step's lint findings (empty when the emit step was ok)
    tests          [{name, module, outcome pass|fail|skip|error, covers[], expects_failure,
                     sim_time_ns, message, file, line, assertion, expected, actual}]
    tests_total    number of tests reported
    waves          project-relative wave file, or null
    coverage       {covered, uncovered, unknown} against spec.yaml, or null without a valid spec
    log            simulator error lines for a failed build or a crashed run
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from siliconsmith.context import ServerContext
from siliconsmith.covers import CocotbTest, coverage, parse_covers
from siliconsmith.frontends import Frontend, get_frontend
from siliconsmith.paths import MissingFileError, project_lock, safe_read
from siliconsmith.project import Project
from siliconsmith.result import Artifact, ToolResult, make_summary
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.docker import ToolchainError, remove_run_dirs
from siliconsmith.tools._common import MAX_WRITE_BYTES, get_project, read_spec_ids_locked
from siliconsmith.tools.emit_verilog import EmitResult, handle_outcome, precheck, read_author
from siliconsmith.worker import limits as lim

SIM_TIMEOUT_S = 600.0
MAX_FILTER_CHARS = 200
SUMMARY_NAMES = 6
WAVES_RE = re.compile(r"^build/waves/(icarus|verilator)/tb\.(fst|vcd)$")
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
    log: list[str] = Field(default_factory=list, max_length=lim.MAX_LOG_LINES)
    exit_code: int | None = None
    message: str | None = Field(default=None, max_length=lim.TEST_TEXT_MAX)


class _RunResult(EmitResult):
    sim: _Sim | None = None


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
            "matched against test names, for example 'wrap' or 'test_count.*'"
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


def _base_details(simulator: str, test_filter: str | None) -> dict[str, Any]:
    return {
        "sim": simulator,
        "filtered": test_filter is not None,
        "test_filter": test_filter,
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
    if sim.state == "build_failed":
        details["stage"] = "build"
        details["log"] = sim.log
        return ToolResult.from_lines(
            False,
            [
                f"run_tests failed: {sim.simulator} could not build the design and testbench.",
                *sim.log,
            ],
            details=details,
            next_hints=[
                f"Fix the first error in {editable} (or in the test files); src/project.v and "
                "test/tb.v are generated or tool-owned. Then call run_tests again."
            ],
        )
    if sim.state == "no_results":
        details["stage"] = "sim"
        details["log"] = sim.log
        return ToolResult.from_lines(
            False,
            [
                f"run_tests failed: {sim.simulator} ended without test results"
                + (f" (exit code {sim.exit_code})." if sim.exit_code is not None else "."),
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
        hint = "The filter is a regular expression matched against test names; check the spelling."
    else:
        line = "The test files contain no cocotb tests (@cocotb.test)."
        hint = (
            "Write tests with write_test: each is an async function decorated with @cocotb.test()."
        )
    return ToolResult.from_lines(
        False,
        [f"run_tests failed on {sim.simulator}: no test ran.", line],
        details=details,
        next_hints=[hint],
    )


def _finish(
    project: Project,
    frontend: Frontend,
    sim: _Sim,
    test_filter: str | None,
    emit_details: dict[str, Any],
) -> ToolResult:
    details = _base_details(sim.simulator, test_filter)
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
        entry["covers"] = list(info.covers) if info else []
        entry["expects_failure"] = info.expects_failure if info else False
        tests.append(entry)
    details["tests"] = tests
    details["tests_total"] = len(tests)
    waves = sim.waves if sim.waves and WAVES_RE.fullmatch(sim.waves) else None
    details["waves"] = waves
    spec_ids = read_spec_ids_locked(project)
    if spec_ids is not None:
        details["coverage"] = coverage(spec_ids, tests).as_dict()
    if not tests:
        return _no_tests(sim, test_filter, details)

    passed, failed = _count(tests, "pass"), _count(tests, "fail") + _count(tests, "error")
    skipped = _count(tests, "skip")
    ok = failed == 0
    verdict = "ok" if ok else "failed"
    scope = f" (filter {test_filter!r})" if test_filter is not None else ""
    lines = [
        f"run_tests {verdict} on {sim.simulator}{scope}: {passed} passed, {failed} failed, "
        f"{skipped} skipped ({len(tests)} tests)."
    ]
    hints: list[str] = []
    bad = [t for t in tests if t["outcome"] in ("fail", "error")]
    if bad:
        lines += _failure_lines(bad[0])
        if len(bad) > 1:
            lines.append(f"Other failing tests: {_name_list(bad[1:], bad[1]['outcome'])}")
        if waves:
            hints.append(
                f"Look at the failing signals with inspect_waves(path={waves!r}, "
                "signals=[...]) around the failure."
            )
        else:
            hints.append(
                f"Rerun with waves=True and test_filter={bad[0]['name']!r}, then use "
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
    if ok and test_filter is None:
        other = "verilator" if sim.simulator == "icarus" else "icarus"
        hints.append(f"Run the tests on {other} as well to check both simulators agree.")
    artifacts = [Artifact(path=waves, kind="waves")] if waves else []
    return ToolResult.from_lines(ok, lines, details=details, artifacts=artifacts, next_hints=hints)


def _timeout(simulator: str, test_filter: str | None, timeout_s: float) -> ToolResult:
    details = _base_details(simulator, test_filter)
    details["stage"] = "sim"
    details["timed_out"] = True
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
) -> ToolResult:
    if simulator not in ("icarus", "verilator"):
        raise ToolError(f"unknown simulator {simulator!r}; choose icarus or verilator")
    test_filter = _check_filter(test_filter)
    project = get_project(ctx, project_name)
    frontend = get_frontend(project.frontend)
    base = _base_details(simulator, test_filter)
    with project_lock(project.dir):
        author = read_author(project)
        early = precheck(project, frontend)
        if early is not None:
            base["stage"] = "elaborate"
            return _retitle(early, base)
        request = {
            **frontend.worker_request(project.name, author),
            "simulator": simulator,
            "test_filter": test_filter,
            "waves": bool(waves),
        }
        timeout_s = SIM_TIMEOUT_S
        try:
            try:
                outcome = ctx.get_runner().run(
                    "test", request, project_dir=project.dir, timeout_s=timeout_s
                )
            except ToolchainError as exc:
                raise ToolError(str(exc)) from exc
            return _handle(
                ctx, project, frontend, author, outcome, simulator, test_filter, timeout_s
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
) -> ToolResult:
    if outcome.timed_out:
        return _timeout(simulator, test_filter, timeout_s)
    # The emit part (files into src/, the failure reports) is shared with emit_verilog.
    emit_report = handle_outcome(ctx, project, frontend, author, outcome)
    base = _base_details(simulator, test_filter)
    if not emit_report.ok:
        base["stage"] = str(emit_report.details.get("stage", "elaborate"))
        return _retitle(emit_report, base)
    assert outcome.result is not None  # handle_outcome accepted it
    try:
        result = _RunResult.model_validate(outcome.result)
    except ValidationError:
        return _bad_sim_result(base, "the simulation part of the result has the wrong shape")
    if result.sim is None or result.sim.simulator != simulator:
        return _bad_sim_result(base, "the result has no simulation part")
    if result.sim.tests_total < len(result.sim.tests):
        return _bad_sim_result(base, "the result contradicts itself")
    return _finish(project, frontend, result.sim, test_filter, emit_report.details)


def _bad_sim_result(details: dict[str, Any], why: str) -> ToolResult:
    details["stage"] = "sim"
    return ToolResult.from_lines(
        False,
        [
            f"run_tests failed: the simulation returned an unreadable result ({why}).",
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
if at least one test ran and none failed. The summary names the first failing test, its \
file:line, the assertion, and expected vs actual. test_filter is a regex matched against test \
names (a filtered run never counts as a full run). waves=True keeps a wave file under \
build/waves/ for inspect_waves. details.tests lists every test with outcome and the \
requirement ids it covers; details.coverage says which spec.yaml requirements have a passing \
test. The first Verilator run takes about half a minute; later runs reuse the build.
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
    ) -> ToolResult:
        return await asyncio.to_thread(run, ctx, project, simulator, test_filter, waves)
