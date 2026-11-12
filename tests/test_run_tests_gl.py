# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""run_tests with gl=True and a fake runner that returns canned worker results (no Docker)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_emit_verilog import RUN_ID, EmitRunner, finding, make, outcome, worker_result
from test_run_tests import FAIL, TESTS_PY, sim_part, sim_test
from test_status import SPEC
from test_synthesize import ESTIMATE, NETLIST, STAT, STATS, synth_part

from siliconsmith.ledger import load_ledger
from siliconsmith.result import summary_within_limits
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.status import compute_status
from siliconsmith.tools import run_tests as rt
from siliconsmith.tools._common import get_project
from siliconsmith.tools.run_tests import run
from siliconsmith.tt_sync import sync_spec_files

DEFINES = {"FUNCTIONAL": "1", "SIM": "1", "UNIT_DELAY": "#1"}
NETLIST_SHA = hashlib.sha256(NETLIST.encode()).hexdigest()
IMAGE = "example/image:tag"


class GlRunner(EmitRunner):
    """Leaves the emit files and the synthesis files in the run folder, like the container."""

    def run(self, verb: str, request: Any, *, project_dir: Path, timeout_s: float) -> WorkerOutcome:
        got = super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)
        out = project_dir / "build" / ".ss" / RUN_ID / "out"
        (out / "synth" / "reports").mkdir(parents=True)
        (out / "synth" / "tt_um_chip.v").write_text(NETLIST)
        (out / "synth" / "reports" / "stat.json").write_text(STATS)
        return got


def gl_part(synth: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "defines": DEFINES,
        "synth": synth if synth is not None else synth_part(),
        "netlist_sha256": "0" * 64,
        "simulated": True,
    }
    base.update(fields)
    return base


def gl_result(
    sim: dict[str, Any] | None, gl: dict[str, Any] | None = None, **fields: Any
) -> dict[str, Any]:
    return worker_result(verb="test", sim=sim, gl=gl if gl is not None else gl_part(), **fields)


def three_pass() -> list[dict[str, Any]]:
    return [sim_test("test_adds"), sim_test("test_expected_failure"), sim_test("test_plain")]


def setup(tmp_path: Path, *outcomes: Any) -> tuple[Any, Path, GlRunner]:
    runner = GlRunner(*outcomes)
    ctx, project_dir = make(tmp_path, "amaranth", runner)
    (project_dir / "test" / "test.py").write_text(TESTS_PY)
    (project_dir / "spec.yaml").write_text(SPEC)
    assert sync_spec_files(get_project(ctx, "chip")).ok
    return ctx, project_dir, runner


def run_gl(ctx: Any, **kw: Any) -> Any:
    args: dict[str, Any] = {"test_filter": None, "waves": False}
    args.update(kw)
    return run(ctx, "chip", "icarus", args["test_filter"], args["waves"], gl=True)


def test_a_passing_gate_level_run(tmp_path: Path) -> None:
    sim = sim_part(three_pass(), waves="build/waves/icarus-gl/tb.fst")
    ctx, project, runner = setup(tmp_path, outcome(gl_result(sim)))
    result = run_gl(ctx, waves=True)
    assert result.ok, result.summary
    lines = result.summary.splitlines()
    assert lines[0] == (
        "Gate-level simulation (sky130 netlist, icarus): ok, 3 passed, 0 failed, "
        "0 skipped (3 tests)."
    )
    assert "Waves: build/waves/icarus-gl/tb.fst" in result.summary
    assert "verilator" not in result.summary.lower()
    assert not any("verilator" in h.lower() for h in result.next_hints)
    assert "Requirements covered" not in result.summary  # never counts for coverage
    details = result.details
    assert details["stage"] == "sim" and details["sim"] == "icarus"
    assert details["waves"] == "build/waves/icarus-gl/tb.fst" and details["coverage"] is None
    assert details["gl"]["defines"] == DEFINES
    assert details["gl"]["netlist_sha256"] == NETLIST_SHA  # hashed here, from the run folder
    assert details["gl"]["synth"]["cells"] == STAT["cells"]
    assert details["gl"]["synth"]["verdict"] == "ok"
    assert [a.path for a in result.artifacts] == ["build/waves/icarus-gl/tb.fst"]
    # One container call, with the synthesis numbers and the longer time limit.
    ((verb, request, _, timeout),) = runner.calls
    assert verb == "test" and request["gl"] is True and request["simulator"] == "icarus"
    assert request["clock_period_ns"] == 100  # 10 MHz from spec.yaml
    assert request["stop_area_um2"] > 0
    assert request["timeout_s"] == rt.SYNTH_TIMEOUT_S
    assert timeout == rt.SYNTH_TIMEOUT_S + rt.SIM_TIMEOUT_S
    # The netlist is not copied out: build/synth belongs to synthesize.
    assert not (project / "build" / "synth").exists()
    assert not list((project / "build" / ".ss").glob("*"))


def test_an_rtl_wave_path_is_not_accepted_for_a_gl_run(tmp_path: Path) -> None:
    sim = sim_part(three_pass(), waves="build/waves/icarus/tb.fst")
    ctx, _, _ = setup(tmp_path, outcome(gl_result(sim)))
    assert run_gl(ctx, waves=True).details["waves"] is None


def test_a_gl_wave_path_is_not_accepted_for_an_rtl_run(tmp_path: Path) -> None:
    ctx, _, _ = setup(
        tmp_path, outcome(worker_result(verb="test", sim=sim_part(
            three_pass(), waves="build/waves/icarus-gl/tb.fst")))
    )  # fmt: skip
    assert run(ctx, "chip", "icarus", None, True).details["waves"] is None


def test_a_failing_gate_level_run_says_gate_level_everywhere(tmp_path: Path) -> None:
    sim = sim_part([sim_test("test_plain"), FAIL])
    ctx, _, _ = setup(tmp_path, outcome(gl_result(sim)))
    result = run_gl(ctx)
    assert not result.ok
    lines = result.summary.splitlines()
    assert lines[0] == (
        "Gate-level simulation (sky130 netlist, icarus): failed, 1 passed, 1 failed, "
        "0 skipped (2 tests)."
    )
    assert lines[1].startswith("First failure: test_adds")
    assert any("waves=True, gl=True" in h for h in result.next_hints)
    assert not any("verilator" in h.lower() for h in result.next_hints)
    assert result.details["gl"]["netlist_sha256"] == NETLIST_SHA


def test_a_filtered_gate_level_run_is_marked_filtered(tmp_path: Path) -> None:
    ctx, project, _ = setup(tmp_path, outcome(gl_result(sim_part([sim_test("test_adds")]))))
    result = run_gl(ctx, test_filter="adds")
    assert result.ok and result.details["filtered"] is True
    assert "(filter 'adds')" in result.summary.splitlines()[0]
    assert load_ledger(project).ledger.runs[0].filtered is True


@pytest.mark.parametrize(
    ("sim", "word"),
    [
        (sim_part([], state="build_failed", log=["error: x"]), "could not build"),
        (sim_part([], state="no_results", log=[]), "without test results"),
        (sim_part([]), "no test ran"),
        (sim_part([sim_test("test_a", "skip")]), "skipped"),
    ],
)
def test_other_gate_level_sim_problems_are_labelled(tmp_path: Path, sim: Any, word: str) -> None:
    ctx, _, _ = setup(tmp_path, outcome(gl_result(sim)))
    result = run_gl(ctx)
    assert not result.ok
    first = result.summary.splitlines()[0]
    assert (
        first.startswith("Gate-level simulation (sky130 netlist, icarus): failed") and word in first
    )
    assert not any("verilator" in h.lower() for h in result.next_hints)


@pytest.mark.parametrize(
    ("synth", "stage", "word"),
    [
        (
            synth_part(state="too_big", stat=None, estimate={**ESTIMATE, "generic_area_um2": 9e5}),
            "too_big",
            "Too big for 1x1",
        ),
        (
            synth_part(state="yosys_failed", stat=None, estimate=None, log=["ERROR: bad"]),
            "synth_failed",
            "Yosys stopped",
        ),
        (
            synth_part(state="timeout", stat=None, estimate=None, message="mapping ran out"),
            "timeout",
            "ran out of time",
        ),
    ],
)
def test_a_synthesis_stop_ends_the_run_with_its_stage(
    tmp_path: Path, synth: dict[str, Any], stage: str, word: str
) -> None:
    ctx, project, _ = setup(
        tmp_path, outcome(gl_result(None, gl_part(synth, simulated=False, netlist_sha256=None)))
    )
    result = run_gl(ctx)
    assert not result.ok
    lines = result.summary.splitlines()
    assert lines[0] == (
        "Gate-level simulation (sky130 netlist, icarus) could not run: synthesis failed, "
        "so there is no netlist to simulate."
    )
    assert word in result.summary
    assert any("synthesize" in h for h in result.next_hints)
    assert not any("verilator" in h.lower() for h in result.next_hints)
    assert result.details["stage"] == stage and result.details["gl"]["netlist_sha256"] is None
    assert summary_within_limits(result.summary)
    record = load_ledger(project).ledger.runs[-1]
    assert (record.gl, record.ok, record.stage) == (True, False, stage)
    assert record.tests == []
    states = {s.key: s.state for s in compute_status(get_project(ctx, "chip"), ctx.image).steps}
    assert states["gate_sim"] == "fail"


def test_a_netlist_check_problem_ends_the_run(tmp_path: Path) -> None:
    check = {
        "signal": "q", "file": "src/chip.v", "line": 7, "kind": "multiple_drivers",
        "message": "q is driven twice",
    }  # fmt: skip
    synth = synth_part(checks=[check], forbidden_cells=["sky130_fd_sc_hd__ha_1"])
    ctx, _, _ = setup(tmp_path, outcome(gl_result(None, gl_part(synth, simulated=False))))
    result = run_gl(ctx)
    assert not result.ok
    assert "could not run: synthesis found a problem" in result.summary.splitlines()[0]
    assert "uses ha_1" in result.summary and "src/chip.v:7: multiple drivers" in result.summary
    assert result.details["stage"] == "synth_failed"
    assert result.details["gl"]["checks"][0]["kind"] == "multiple_drivers"


def test_a_too_big_netlist_still_simulates_but_says_so(tmp_path: Path) -> None:
    big = dict(STAT, area_um2=90000.0)
    ctx, _, _ = setup(
        tmp_path, outcome(gl_result(sim_part(three_pass()), gl_part(synth_part(stat=big))))
    )
    result = run_gl(ctx)
    assert result.ok
    assert "Note: the gates would not fit a 1x1 tile" in result.summary
    assert result.details["gl"]["synth"]["verdict"] == "too_big"
    assert "warnings" not in result.details["gl"]


def test_a_container_timeout_is_reported_for_both_stages(tmp_path: Path) -> None:
    ctx, project, _ = setup(tmp_path, outcome(None, timed_out=True, exit_code=None))
    result = run_gl(ctx)
    assert not result.ok and result.details["timed_out"] is True
    assert result.summary.startswith("Gate-level simulation (sky130 netlist, icarus): failed")
    assert "synthesis and simulation together" in result.summary
    record = load_ledger(project).ledger.runs[-1]
    assert (record.gl, record.stage, record.ok) == (True, "timeout", False)


def test_an_emit_failure_inside_a_gl_run_is_an_emit_report_marked_gl(tmp_path: Path) -> None:
    failed = worker_result(verb="test", ok=False, stage="lint", lint=[finding(1)], lint_total=1)
    ctx, project, _ = setup(tmp_path, outcome(failed))
    result = run_gl(ctx)
    assert not result.ok and result.details["stage"] == "lint"
    assert result.summary.startswith("run_tests did not simulate: the emit step failed")
    record = load_ledger(project).ledger.runs[-1]
    assert record.gl is True and record.stage == "lint"
    # Failing the emit step says nothing about the gates.
    states = {s.key: s.state for s in compute_status(get_project(ctx, "chip"), ctx.image).steps}
    assert states["gate_sim"] == "not_run"


@pytest.mark.parametrize(
    "bad",
    [
        worker_result(verb="test", sim=sim_part(three_pass())),  # no gl part
        gl_result(None),  # synthesis ran and was clean, but there is no simulation
        gl_result(sim_part(three_pass()), gl_part(synth_part(stat=None))),
        gl_result(sim_part(three_pass(), simulator="verilator")),
    ],
    ids=["no-gl", "no-sim", "no-stat", "wrong-simulator"],
)
def test_unreadable_gate_level_results_are_reported(tmp_path: Path, bad: dict[str, Any]) -> None:
    ctx, _, _ = setup(tmp_path, outcome(bad))
    result = run_gl(ctx)
    assert not result.ok and "unreadable result" in result.summary


def test_a_missing_netlist_file_is_reported_not_trusted(tmp_path: Path) -> None:
    class NoNetlist(GlRunner):
        def run(self, verb: str, request: Any, *, project_dir: Path, timeout_s: float) -> Any:
            got = super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)
            (project_dir / "build" / ".ss" / RUN_ID / "out" / "synth" / "tt_um_chip.v").unlink()
            return got

    runner = NoNetlist(outcome(gl_result(sim_part(three_pass()))))
    ctx, _ = make(tmp_path, "amaranth", runner)
    project = get_project(ctx, "chip")
    (project.dir / "test" / "test.py").write_text(TESTS_PY)
    (project.dir / "spec.yaml").write_text(SPEC)
    assert sync_spec_files(project).ok
    result = run_gl(ctx)
    assert not result.ok and "result file is missing" in result.summary


# -- the ledger and status -----------------------------------------------------------------


def test_a_gl_record_carries_both_digests_and_the_gl_flag(tmp_path: Path) -> None:
    ctx, project, _ = setup(
        tmp_path,
        outcome(gl_result(sim_part(three_pass()))),
        outcome(worker_result(verb="test", sim=sim_part(three_pass()))),
    )
    assert run_gl(ctx).ok
    assert run(ctx, "chip", "icarus", None, False).ok
    gl_record, rtl_record = load_ledger(project).ledger.runs
    assert (gl_record.tool, gl_record.sim, gl_record.gl, gl_record.stage) == (
        "run_tests", "icarus", True, "sim",
    )  # fmt: skip
    assert gl_record.digests.sim and gl_record.digests.synth
    assert rtl_record.gl is False
    # Facts only: no verdict in the record, and the netlist hash is not a ledger field.
    assert "netlist" not in gl_record.model_dump_json()
    states = {s.key: s for s in compute_status(get_project(ctx, "chip"), ctx.image).steps}
    # The Icarus row is the RTL run, and gate_sim is the gate-level one.
    assert states["sim_icarus"].evidence == "run_tests icarus: 3/3 passed"
    assert states["gate_sim"].state == "pass"


def test_the_spec_clock_is_synced_before_the_digests_are_taken(tmp_path: Path) -> None:
    """A hand-stale src/config.json is fixed first, so the recorded synth digest is current."""
    ctx, project, runner = setup(tmp_path, outcome(gl_result(sim_part(three_pass()))))
    spec = project / "spec.yaml"
    spec.write_text(spec.read_text().replace("clock_hz: 10000000", "clock_hz: 50000000"))
    assert '"CLOCK_PERIOD": 100' in (project / "src" / "config.json").read_text()
    assert run_gl(ctx).ok
    assert '"CLOCK_PERIOD": 20' in (project / "src" / "config.json").read_text()
    ((_, request, _, _),) = runner.calls
    assert request["clock_period_ns"] == 20
    states = {s.key: s.state for s in compute_status(get_project(ctx, "chip"), ctx.image).steps}
    assert states["gate_sim"] == "pass"  # not stale: the snapshot came after the sync


def test_gl_without_a_usable_spec_is_an_error(tmp_path: Path) -> None:
    ctx, project, runner = setup(tmp_path)
    (project / "spec.yaml").write_text("title: x\n")
    with pytest.raises(ToolError):
        run_gl(ctx)
    assert runner.calls == []


def test_gl_with_verilator_is_refused_without_a_hint_to_use_verilator(tmp_path: Path) -> None:
    ctx, _, runner = setup(tmp_path)
    with pytest.raises(ToolError, match="gate-level simulation runs on icarus only") as err:
        run(ctx, "chip", "verilator", None, False, gl=True)
    assert "use verilator" not in str(err.value).lower()
    assert runner.calls == []


GL_WAVES = "build/waves/icarus-gl/tb.fst"
DIFFERS = {
    "state": "differs",
    "cycle": 3,
    "time_ns": 30.0,
    "cycles_compared": 3,
    "signals": [
        {"signal": "uo_out", "rtl": "00000001", "gl": "xxxxxxxx"},
        {"signal": "uio_oe", "rtl": "00000000", "gl": "xxxxxxxx"},
    ],
    "rtl_failed": False,
}


def failing_gl(tmp_path: Path, diff: dict[str, Any] | None, waves: str | None = GL_WAVES) -> Any:
    sim = sim_part([sim_test("test_plain"), FAIL], waves=waves)
    ctx, _, _ = setup(tmp_path, outcome(gl_result(sim, gl_part(diff=diff, seed=7))))
    return run_gl(ctx)


def test_a_failing_gate_level_run_reports_the_first_difference_on_the_pins(tmp_path: Path) -> None:
    result = failing_gl(tmp_path, DIFFERS)
    lines = result.summary.splitlines()
    assert (
        "First difference on the pins: uo_out at clock cycle 3 (t=30 ns): "
        "RTL 00000001, gate level xxxxxxxx (uio_oe differ at the same cycle)."
    ) in lines
    assert any("have no reset or rely on an initial value" in ln for ln in lines)
    assert len(lines) <= 20 and summary_within_limits(result.summary)
    assert result.details["gl"]["diff"]["signals"][0]["signal"] == "uo_out"
    assert result.details["gl"]["diff"]["cycle"] == 3
    assert result.details["waves"] == GL_WAVES
    hint = next(h for h in result.next_hints if GL_WAVES in h)
    assert "inspect_waves" in hint and "tb pins" in hint and "core.count[0]" in hint
    assert not any("verilator" in h.lower() for h in result.next_hints)


def test_the_report_says_when_the_rtl_fails_too(tmp_path: Path) -> None:
    result = failing_gl(tmp_path, {**DIFFERS, "rtl_failed": True})
    assert "The same tests also fail on the RTL, so look at the design or the test first." in (
        result.summary
    )


def test_the_report_says_plainly_when_the_pins_never_differ(tmp_path: Path) -> None:
    same = {"state": "same", "cycles_compared": 12}
    result = failing_gl(tmp_path, same)
    assert "match the RTL at every clock edge until the failure (12 cycles compared)" in (
        result.summary
    )
    assert "not a pin" in result.summary


@pytest.mark.parametrize("state", ["skipped", "unavailable", "no_clock"])
def test_a_missing_comparison_is_one_plain_line(tmp_path: Path, state: str) -> None:
    result = failing_gl(tmp_path, {"state": state, "reason": "the RTL rerun failed"})
    assert "No RTL-versus-gate-level comparison: the RTL rerun failed." in result.summary


def test_a_passing_gate_level_run_has_no_diff(tmp_path: Path) -> None:
    ctx, _, _ = setup(tmp_path, outcome(gl_result(sim_part(three_pass()))))
    assert run_gl(ctx).details["gl"]["diff"] is None
