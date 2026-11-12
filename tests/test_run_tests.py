# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""run_tests with a fake runner that returns canned worker results (no Docker)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_emit_verilog import (
    CORE,
    RUN_ID,
    EmitRunner,
    make,
    outcome,
    worker_result,
)

from siliconsmith.result import summary_within_limits
from siliconsmith.runners.docker import DockerUnavailableError, ImageMissingError
from siliconsmith.tools import run_tests as rt
from siliconsmith.tools.run_tests import run
from siliconsmith.wrapper import generate_wrapper

TESTS_PY = '''\
import cocotb


@cocotb.test()
async def test_adds(dut):
    """Covers: R1"""


@cocotb.test(expect_fail=True)
async def test_expected_failure(dut):
    """Covers: R1"""


@cocotb.test()
async def test_plain(dut):
    pass
'''


def sim_test(name: str, outcome_: str = "pass", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": name,
        "module": "test",
        "outcome": outcome_,
        "sim_time_ns": 10.0,
        "message": None,
        "file": None,
        "line": None,
        "assertion": None,
        "expected": None,
        "actual": None,
    }
    base.update(fields)
    return base


def sim_part(tests: list[dict[str, Any]], **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "ran",
        "simulator": "icarus",
        "tests": tests,
        "tests_total": len(tests),
        "waves": None,
        "log": [],
        "exit_code": 0,
        "message": None,
    }
    base.update(fields)
    return base


def sim_result(sim: dict[str, Any] | None, **fields: Any) -> dict[str, Any]:
    return worker_result(verb="test", sim=sim, **fields)


def setup(
    tmp_path: Path, *outcomes: Any, frontend: str = "amaranth"
) -> tuple[Any, Path, EmitRunner]:
    runner = EmitRunner(*outcomes)
    ctx, project = make(tmp_path, frontend, runner)
    (project / "test" / "test.py").write_text(TESTS_PY)
    return ctx, project, runner


FAIL = sim_test(
    "test_adds",
    "fail",
    file="test/test.py",
    line=9,
    assertion="assert int(dut.uo_out.value) == 50",
    expected="50",
    actual="0",
)


def test_pass(tmp_path: Path) -> None:
    tests = [sim_test("test_adds"), sim_test("test_expected_failure"), sim_test("test_plain")]
    ctx, project, runner = setup(tmp_path, outcome(sim_result(sim_part(tests))))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok, result.summary
    assert result.summary.splitlines()[0] == (
        "run_tests ok on icarus: 3 passed, 0 failed, 0 skipped (3 tests)."
    )
    details = result.details
    assert (details["sim"], details["stage"], details["filtered"]) == ("icarus", "sim", False)
    assert [(t["name"], t["covers"]) for t in details["tests"]] == [
        ("test_adds", ["R1"]),
        ("test_expected_failure", ["R1"]),
        ("test_plain", []),
    ]
    assert details["tests"][1]["expects_failure"] is True
    assert details["coverage"] == {"covered": ["R1"], "uncovered": [], "unknown": []}
    assert details["waves"] is None and details["lint"] == []
    # The emit step's files were written like emit_verilog does.
    assert (project / "src" / "chip.v").read_text() == CORE
    assert (project / "src" / "project.v").read_text() == generate_wrapper(
        "chip", {"ui_in": "input", "uo_out": "output"}, "Ada Lovelace"
    )
    ((verb, request, _, timeout),) = runner.calls
    assert verb == "test" and timeout == 600.0
    assert request == {
        "name": "chip",
        "frontend": "amaranth",
        "author": "Ada Lovelace",
        "simulator": "icarus",
        "test_filter": None,
        "waves": False,
    }
    assert not list((project / "build" / ".ss").glob("*"))
    assert any("verilator" in hint for hint in result.next_hints)


def test_expect_fail_test_never_covers(tmp_path: Path) -> None:
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim_part([sim_test("test_expected_failure")]))))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok
    assert result.details["coverage"]["uncovered"] == ["R1"]
    assert "Not covered yet: R1" in result.summary


def test_failure_names_test_assertion_expected_and_actual(tmp_path: Path) -> None:
    tests = [FAIL, sim_test("test_plain"), sim_test("test_expected_failure", "fail", message="x")]
    ctx, _, _ = setup(
        tmp_path, outcome(sim_result(sim_part(tests, waves="build/waves/icarus/tb.fst")))
    )
    result = run(ctx, "chip", "icarus", None, True)
    assert result.ok is False
    lines = result.summary.splitlines()
    assert len(lines) <= 20 and summary_within_limits(result.summary)
    assert lines[0] == "run_tests failed on icarus: 1 passed, 2 failed, 0 skipped (3 tests)."
    assert lines[1] == "First failure: test_adds (test/test.py:9)"
    assert "  assertion: assert int(dut.uo_out.value) == 50" in lines
    assert "  expected: 50" in lines and "  actual:   0" in lines
    assert "Other failing tests: test_expected_failure" in lines
    assert result.details["waves"] == "build/waves/icarus/tb.fst"
    assert [a.path for a in result.artifacts] == ["build/waves/icarus/tb.fst"]
    assert "inspect_waves" in result.next_hints[0]
    assert result.details["coverage"]["uncovered"] == ["R1"]


def test_failure_without_waves_hints_at_waves(tmp_path: Path) -> None:
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim_part([FAIL], simulator="verilator"))))
    result = run(ctx, "chip", "verilator", None, False)
    assert "waves=True" in result.next_hints[0] and "'test_adds'" in result.next_hints[0]


def test_a_bogus_wave_path_is_dropped(tmp_path: Path) -> None:
    sim = sim_part([sim_test("test_adds")], waves="build/waves/../../spec.yaml")
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim)))
    result = run(ctx, "chip", "icarus", None, True)
    assert result.details["waves"] is None and result.artifacts == []


def test_filtered_run_is_marked_and_skips_coverage_lines(tmp_path: Path) -> None:
    ctx, _, runner = setup(tmp_path, outcome(sim_result(sim_part([sim_test("test_adds")]))))
    result = run(ctx, "chip", "icarus", "test_ad.*", False)
    assert result.ok
    assert result.details["filtered"] is True and result.details["test_filter"] == "test_ad.*"
    assert "(filter 'test_ad.*')" in result.summary
    assert "Requirements covered" not in result.summary
    assert runner.calls[0][1]["test_filter"] == "test_ad.*"
    assert any("without test_filter" in h for h in result.next_hints)


def test_blank_filter_means_no_filter(tmp_path: Path) -> None:
    ctx, _, runner = setup(tmp_path, outcome(sim_result(sim_part([sim_test("test_adds")]))))
    result = run(ctx, "chip", "icarus", "   ", False)
    assert result.details["filtered"] is False and runner.calls[0][1]["test_filter"] is None


def test_zero_tests_is_a_failure(tmp_path: Path) -> None:
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim_part([]))))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok is False and "no test ran" in result.summary
    assert "no cocotb tests" in result.summary
    (tmp_path / "b").mkdir()
    ctx, _, _ = setup(tmp_path / "b", outcome(sim_result(sim_part([]))))
    filtered = run(ctx, "chip", "icarus", "zzz", False)
    assert filtered.ok is False and "matched no test" in filtered.summary


def test_skipped_only_is_ok(tmp_path: Path) -> None:
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim_part([sim_test("test_plain", "skip")]))))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok and "0 passed, 0 failed, 1 skipped" in result.summary


def test_emit_failure_means_no_simulation(tmp_path: Path) -> None:
    finding = {
        "severity": "warning",
        "code": "WIDTHTRUNC",
        "file": "src/chip.v",
        "line": 3,
        "col": 5,
        "msg": "too wide",
        "detail": "",
    }
    bad = sim_result(None, ok=False, stage="lint", lint=[finding], lint_total=1)
    ctx, _, _ = setup(tmp_path, outcome(bad))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok is False
    assert result.summary.startswith("run_tests did not simulate: the emit step failed")
    assert "WIDTHTRUNC" in result.summary
    assert result.details["stage"] == "lint" and result.details["tests"] == []
    assert result.details["lint"][0]["code"] == "WIDTHTRUNC"
    assert all("emit_verilog" not in h for h in result.next_hints)


def test_python_syntax_error_is_found_without_a_container(tmp_path: Path) -> None:
    ctx, project, runner = setup(tmp_path)
    (project / "hdl" / "chip.py").write_text("def top(:\n    pass\n")
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok is False and "hdl/chip.py:1" in result.summary
    assert runner.calls == []
    assert result.details["stage"] == "elaborate"


def test_compile_error(tmp_path: Path) -> None:
    sim = sim_part(
        [],
        simulator="verilator",
        state="build_failed",
        log=["%Error: src/project.v:12:3: syntax error, unexpected IDENTIFIER"],
    )
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim)))
    result = run(ctx, "chip", "verilator", None, False)
    assert result.ok is False
    assert "verilator could not build" in result.summary
    assert "src/project.v:12:3" in result.summary
    assert result.details["stage"] == "build" and result.details["log"] == sim["log"]


def test_no_results_file(tmp_path: Path) -> None:
    sim = sim_part([], state="no_results", log=["Segmentation fault"], exit_code=139)
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim)))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok is False and "exit code 139" in result.summary
    assert "Segmentation fault" in result.summary


@pytest.mark.parametrize("state", ["no_modules", "no_testbench"])
def test_project_problems_are_tool_errors(tmp_path: Path, state: str) -> None:
    sim = sim_part([], state=state, message="the thing is missing")
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim)))
    with pytest.raises(ToolError, match="the thing is missing"):
        run(ctx, "chip", "icarus", None, False)


def test_malformed_results(tmp_path: Path) -> None:
    for bad in (
        sim_part([sim_test("test_adds", "maybe")]),  # not an outcome
        sim_part([sim_test("test_adds")], tests_total=0),  # contradicts itself
        sim_part([{"name": "x"}]),
        {**sim_part([]), "simulator": "verilator"},  # not what was asked for
        None,
    ):
        (tmp_path / "x").mkdir()
        ctx, _, _ = setup(tmp_path / "x", outcome(sim_result(bad)))
        result = run(ctx, "chip", "icarus", None, False)
        assert result.ok is False and "unreadable result" in result.summary
        assert result.details["tests"] == []
        assert result.details["stage"] == "sim"
        shutil.rmtree(tmp_path / "x")


def test_no_result_file_at_all(tmp_path: Path) -> None:
    ctx, _, _ = setup(tmp_path, outcome(None, exit_code=137, stderr_tail="Killed"))
    result = run(ctx, "chip", "icarus", None, False)
    assert result.ok is False and "unreadable" in result.summary


def test_timeout_is_a_design_failure(tmp_path: Path) -> None:
    ctx, project, _ = setup(tmp_path, outcome(None, timed_out=True, exit_code=-9))
    result = run(ctx, "chip", "verilator", None, False)
    assert result.ok is False
    assert result.summary.splitlines()[0] == (
        "run_tests failed: simulation did not finish in 600 s (a test may be waiting forever)."
    )
    assert result.details["timed_out"] is True and result.details["tests"] == []
    assert not list((project / "build" / ".ss").glob("*"))


def test_worker_crash_is_a_tool_error(tmp_path: Path) -> None:
    crash = {"ok": False, "verb": "test", "error": {"kind": "ValueError", "message": "libpython"}}
    ctx, _, _ = setup(tmp_path, outcome(crash))
    with pytest.raises(ToolError, match="libpython"):
        run(ctx, "chip", "icarus", None, False)


@pytest.mark.parametrize(
    "error", [DockerUnavailableError("docker is not running"), ImageMissingError("docker pull x")]
)
def test_docker_problems_are_tool_errors(tmp_path: Path, error: Exception) -> None:
    runner = EmitRunner(error=error)
    ctx, project = make(tmp_path, "amaranth", runner)
    with pytest.raises(ToolError, match=str(error)):
        run(ctx, "chip", "icarus", None, False)
    assert not list((project / "build" / ".ss").glob("*"))


def test_bad_arguments(tmp_path: Path) -> None:
    ctx, _, runner = setup(tmp_path)
    with pytest.raises(ToolError, match="unknown simulator"):
        run(ctx, "chip", "modelsim", None, False)  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="regular expression"):
        run(ctx, "chip", "icarus", "(", False)
    with pytest.raises(ToolError, match="longer than"):
        run(ctx, "chip", "icarus", "a" * 300, False)
    with pytest.raises(ToolError):
        run(ctx, "nosuch", "icarus", None, False)
    assert runner.calls == []


def test_summary_stays_within_limits_for_many_failures(tmp_path: Path) -> None:
    tests = [
        sim_test(f"test_{n}_" + "x" * 150, "fail", assertion="assert " + "a" * 250 + " == 1",
                 expected="1", actual="2", file="test/test.py", line=n + 1, message="m" * 290)
        for n in range(40)
    ]  # fmt: skip
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim_part(tests))))
    result = run(ctx, "chip", "icarus", None, False)
    assert summary_within_limits(result.summary)
    assert len(result.details["tests"]) == 40


def test_run_id_unused() -> None:
    assert RUN_ID  # the shared helper module is importable from here
    assert rt.SIM_TIMEOUT_S == 600.0


def test_wave_problem_is_reported(tmp_path: Path) -> None:
    sim = sim_part([sim_test("test_adds")], message="the simulation wrote no wave file")
    ctx, _, _ = setup(tmp_path, outcome(sim_result(sim)))
    result = run(ctx, "chip", "icarus", None, True)
    assert result.ok and "Note: the simulation wrote no wave file" in result.summary
    assert result.details["waves"] is None


@pytest.mark.anyio
async def test_tools_are_registered_and_validate_arguments(tmp_path: Path) -> None:
    from mcp import Client

    from siliconsmith.server import build_server

    server = build_server(tmp_path, None, runner=EmitRunner(), image="example/image:tag")
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
        assert {"run_tests", "inspect_waves"} <= names
        bad = await client.call_tool("run_tests", {"project": "x", "simulator": "modelsim"})
        assert bad.is_error
        missing = await client.call_tool("run_tests", {"project": "nosuch", "simulator": "icarus"})
        assert missing.is_error
