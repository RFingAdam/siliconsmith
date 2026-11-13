# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The `synthesize` tool with a fake runner that returns canned worker results (no Docker)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT, FakeRunner
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.context import ServerContext
from siliconsmith.ledger import load_ledger
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.scaffold import create_project
from siliconsmith.status import compute_status
from siliconsmith.tools import synthesize as tool
from siliconsmith.worker.wrapper_gen import TT_PORTS

RUN_ID = "0123456789abcdef0123456789abcdef"
CORE = "// SPDX-FileCopyrightText: Ada\nmodule chip(input clk, output [7:0] uo_out);\nendmodule\n"
NETLIST = "module tt_um_chip(); endmodule\n"
ALL_PORTS = {p: {"direction": d, "width": w} for p, (d, w) in TT_PORTS.items()}
CORE_PORTS = {
    "ui_in": {"direction": "input", "width": 8},
    "uo_out": {"direction": "output", "width": 8},
}
STATS = (REPO_ROOT / "tests" / "fixtures" / "synth" / "counter_stat.json").read_text()
STAT = {
    "cells": 46,
    "flip_flops": 8,
    "latch_cells": 0,
    "combinational": 38,
    "area_um2": 417.9008,
}
ESTIMATE = {"generic_area_um2": 351.0, "generic_cells": 24, "stop_area_um2": 29688.0}
TOP = [
    {"cell": "sky130_fd_sc_hd__conb_1", "count": 16},
    {"cell": "sky130_fd_sc_hd__dfxtp_2", "count": 8},
]


def synth_part(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": "ran",
        "yosys_seconds": 4.6,
        "message": None,
        "log": [],
        "estimate": ESTIMATE,
        "stat": STAT,
        "top_cells": TOP,
        "forbidden_cells": [],
        "latches": [],
        "checks": [],
        "netlist": "synth/tt_um_chip.v",
        "netlist_sha256": "0" * 64,
    }
    base.update(fields)
    return base


def worker_result(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "verb": "synth",
        "stage": "done",
        "run_id": RUN_ID,
        "ports": CORE_PORTS,
        "wrapper_ports": ALL_PORTS,
        "lint": [],
        "lint_total": 0,
        "python_error": None,
        "message": None,
        "core_generated": True,
        "synth": synth_part(),
    }
    base.update(fields)
    return base


def outcome(result: dict[str, Any] | None, **fields: Any) -> WorkerOutcome:
    values: dict[str, Any] = {
        "exit_code": 0,
        "result": result,
        "stdout_tail": "",
        "stderr_tail": "",
        "timed_out": False,
        "run_id": RUN_ID,
    }
    values.update(fields)
    return WorkerOutcome(**values)


class SynthRunner(FakeRunner):
    """Leaves the worker's output files under build/.ss/<id>/out like the real container."""

    def __init__(self, *outcomes: WorkerOutcome, netlist: str | None = NETLIST) -> None:
        super().__init__(*outcomes)
        self.netlist = netlist

    def run(self, verb: str, request: Any, *, project_dir: Path, timeout_s: float) -> WorkerOutcome:
        out = project_dir / "build" / ".ss" / RUN_ID / "out"
        (out / "synth" / "reports").mkdir(parents=True)
        (out / "chip.v").write_text(CORE)
        (out / "project.v").write_text("// the container's own wrapper: must never be copied\n")
        if self.netlist is not None:
            (out / "synth" / "tt_um_chip.v").write_text(self.netlist)
        (out / "synth" / "reports" / "stat.json").write_text(STATS)
        return super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)


def make(
    tmp_path: Path, runner: FakeRunner, frontend: str = "verilog"
) -> tuple[ServerContext, Any]:
    project, _ = create_project(tmp_path, "chip", frontend, "Ada Lovelace", "A chip.")
    return ServerContext(root=tmp_path, default_frontend=None, runner=runner), project


def test_success_saves_the_netlist_and_reports_the_numbers(tmp_path: Path) -> None:
    runner = SynthRunner(outcome(worker_result()))
    ctx, project = make(tmp_path, runner)
    result = tool.synthesize(ctx, "chip")
    assert result.ok, result.summary
    first = result.summary.splitlines()[0]
    assert first == (
        "Synthesis passed: 46 cells (8 flip-flops, 38 logic), 417.9 um^2, about 4% of a 1x1 "
        "tile (estimate; layout (M2) gives the real number)."
    )
    build = project.dir / "build" / "synth"
    assert (build / "tt_um_chip.v").read_text() == NETLIST
    assert (build / "stat.json").read_text() == STATS
    details = result.details
    assert details["stage"] == "synth"
    assert details["cells"] == 46 and details["flip_flops"] == 8 and details["combinational"] == 38
    assert details["area_um2"] == pytest.approx(417.901)
    assert details["top_cells"] == TOP
    assert details["utilization"]["verdict"] == "ok" and details["utilization"]["tiles"] == "1x1"
    assert details["utilization"]["estimate"] == pytest.approx(0.0422, abs=1e-4)
    assert details["latches"] == [] and details["checks"] == [] and details["warnings"] == []
    assert details["netlist"] == "build/synth/tt_um_chip.v"
    # The hash is of the bytes the host copied, not what the container claimed.
    assert details["netlist_sha256"] == hashlib.sha256(NETLIST.encode()).hexdigest()
    assert details["yosys_seconds"] == 4.6
    assert details["clock_period_ns"] == 100  # the stub spec runs at 10 MHz
    assert {a.path for a in result.artifacts} >= {
        "src/chip.v",
        "src/project.v",
        "build/synth/tt_um_chip.v",
        "build/synth/stat.json",
    }
    assert "never be copied" not in (project.dir / "src" / "project.v").read_text()
    assert not list((project.dir / "build" / ".ss").glob("*"))  # the run folder is gone


def test_the_request_carries_clock_budget_and_timeout(tmp_path: Path) -> None:
    runner = SynthRunner(outcome(worker_result()))
    ctx, project = make(tmp_path, runner)
    tool.synthesize(ctx, "chip")
    ((verb, request, run_project, timeout),) = runner.calls
    assert (verb, run_project, timeout) == ("synth", project.dir, 900.0)
    assert tool.SYNTH_TIMEOUT_S == 900.0
    assert request["name"] == "chip" and request["frontend"] == "verilog"
    assert request["clock_period_ns"] == 100
    # 3 times the 1x1 cell budget (see utilization.py)
    assert request["stop_area_um2"] == pytest.approx(3 * 9895.99, rel=1e-4)
    assert request["timeout_s"] == 900.0


def test_the_clock_and_tile_come_from_the_spec_every_time(tmp_path: Path) -> None:
    runner = SynthRunner(outcome(worker_result()))
    ctx, project = make(tmp_path, runner)
    spec = project.dir / "spec.yaml"
    spec.write_text(spec.read_text().replace("clock_hz: 10000000", "clock_hz: 50000000"))
    config = project.dir / "src" / "config.json"
    assert '"CLOCK_PERIOD": 100' in config.read_text()
    tool.synthesize(ctx, "chip")
    assert '"CLOCK_PERIOD": 20' in config.read_text()  # re-synced before the run
    assert runner.calls[0][1]["clock_period_ns"] == 20


def test_a_spec_that_cannot_be_used_is_a_tool_error_and_runs_nothing(tmp_path: Path) -> None:
    runner = SynthRunner()
    ctx, project = make(tmp_path, runner)
    (project.dir / "spec.yaml").write_text("title: x\n")
    with pytest.raises(ToolError, match="cannot continue"):
        tool.synthesize(ctx, "chip")
    assert runner.calls == []


def test_above_66_mhz_it_warns(tmp_path: Path) -> None:
    runner = SynthRunner(outcome(worker_result()))
    ctx, project = make(tmp_path, runner)
    spec = project.dir / "spec.yaml"
    spec.write_text(spec.read_text().replace("clock_hz: 10000000", "clock_hz: 100000000"))
    result = tool.synthesize(ctx, "chip")
    assert result.ok
    assert any("66 MHz" in w for w in result.details["warnings"])
    assert any(
        line.startswith("Warning:") and "66 MHz" in line for line in result.summary.splitlines()
    )


def test_a_latch_is_a_warning_with_file_and_line(tmp_path: Path) -> None:
    latch = {"signal": "held", "file": "src/chip.v", "line": 10}
    ctx, _ = make(tmp_path, SynthRunner(outcome(worker_result(synth=synth_part(latches=[latch])))))
    result = tool.synthesize(ctx, "chip")
    assert result.ok, result.summary
    assert result.details["latches"][0]["line"] == 10
    (warning,) = result.details["warnings"]
    assert "held" in warning and "src/chip.v:10" in warning
    assert any("src/chip.v:10" in ln for ln in result.summary.splitlines())


def test_a_myhdl_latch_names_the_hdl_function(tmp_path: Path) -> None:
    latch = {
        "signal": "held",
        "file": "src/chip.v",
        "line": 39,
        "hdl_file": "hdl/chip.py",
        "hdl_line": 19,
        "hdl_function": "remember",
    }
    ctx, _ = make(
        tmp_path, SynthRunner(outcome(worker_result(synth=synth_part(latches=[latch])))), "myhdl"
    )
    result = tool.synthesize(ctx, "chip")
    (warning,) = result.details["warnings"]
    assert "hdl/chip.py:19" in warning and "remember" in warning


@pytest.mark.parametrize(
    ("kind", "word"),
    [
        ("comb_loop", "comb loop"),
        ("multiple_drivers", "multiple drivers"),
        ("undriven", "undriven"),
    ],
)
def test_netlist_problems_fail_with_their_source_location(
    tmp_path: Path, kind: str, word: str
) -> None:
    check = {"kind": kind, "signal": "x", "file": "src/chip.v", "line": 7, "message": "a problem"}
    runner = SynthRunner(outcome(worker_result(synth=synth_part(checks=[check]))))
    ctx, project = make(tmp_path, runner)
    result = tool.synthesize(ctx, "chip")
    assert not result.ok
    assert result.details["stage"] == "synth_failed"
    assert any(f"src/chip.v:7: {word}" in ln for ln in result.summary.splitlines())
    assert result.details["checks"][0]["line"] == 7
    assert (project.dir / "build" / "synth" / "tt_um_chip.v").exists()


@pytest.mark.parametrize(
    "cell", ["sky130_fd_sc_hd__dfxtp_1", "sky130_fd_sc_hd__lpflow_isobufsrc_1"]
)
def test_a_forbidden_cell_in_the_netlist_fails(tmp_path: Path, cell: str) -> None:
    ctx, _ = make(
        tmp_path, SynthRunner(outcome(worker_result(synth=synth_part(forbidden_cells=[cell]))))
    )
    result = tool.synthesize(ctx, "chip")
    assert not result.ok and result.details["stage"] == "synth_failed"
    assert cell.removeprefix("sky130_fd_sc_hd__") in result.summary
    assert result.details["forbidden_cells"] == [cell]


def test_more_than_the_budget_after_mapping_fails_as_too_big(tmp_path: Path) -> None:
    stat = {**STAT, "area_um2": 12000.0}
    ctx, project = make(tmp_path, SynthRunner(outcome(worker_result(synth=synth_part(stat=stat)))))
    result = tool.synthesize(ctx, "chip")
    assert not result.ok
    assert result.details["stage"] == "too_big"
    assert result.details["utilization"]["verdict"] == "too_big"
    assert "Too big for 1x1" in result.summary
    assert any("bigger tile" in h and "owner" in h for h in result.next_hints)
    assert (project.dir / "build" / "synth" / "tt_um_chip.v").exists()


def test_close_to_the_budget_still_passes_with_a_warning(tmp_path: Path) -> None:
    stat = {**STAT, "area_um2": 9500.0}
    ctx, _ = make(tmp_path, SynthRunner(outcome(worker_result(synth=synth_part(stat=stat)))))
    result = tool.synthesize(ctx, "chip")
    assert result.ok and result.details["utilization"]["verdict"] == "warn"
    assert any("90%" in w for w in result.details["warnings"])


def test_a_design_far_over_budget_stops_before_abc_with_a_plain_message(tmp_path: Path) -> None:
    estimate = {"generic_area_um2": 217168.2, "generic_cells": 26122, "stop_area_um2": 29688.0}
    part = synth_part(state="too_big", estimate=estimate, stat=None, netlist=None,
                      netlist_sha256=None, yosys_seconds=9.9, top_cells=[])  # fmt: skip
    runner = SynthRunner(outcome(worker_result(synth=part)), netlist=None)
    ctx, project = make(tmp_path, runner)
    result = tool.synthesize(ctx, "chip")
    assert not result.ok
    lines = result.summary.splitlines()
    assert lines[0].startswith("Too big for 1x1: synthesis was stopped early")
    assert "217168 um2" in lines[1] and "9896 um2" in lines[1] and "21.9 times" in lines[1]
    assert result.details["stage"] == "too_big"
    assert result.details["synth"] == {"tiles": "1x1", "verdict": "too_big"}
    assert "make the design smaller" in result.next_hints[0].lower()
    assert "ask the owner about a bigger tile" in result.next_hints[1]
    assert not (project.dir / "build" / "synth").exists()  # no netlist was made
    ledger = load_ledger(project.dir).ledger
    assert ledger.runs[-1].stage == "too_big" and not ledger.runs[-1].ok


def test_yosys_errors_are_shown_with_their_lines(tmp_path: Path) -> None:
    log = ["src/chip.v:3: ERROR: syntax error"]
    part = synth_part(state="yosys_failed", stat=None, netlist=None, log=log, message="Yosys")
    ctx, _ = make(tmp_path, SynthRunner(outcome(worker_result(synth=part)), netlist=None))
    result = tool.synthesize(ctx, "chip")
    assert not result.ok and result.details["stage"] == "synth_failed"
    assert "src/chip.v:3: ERROR: syntax error" in result.summary


def test_a_worker_side_timeout_is_reported_plainly(tmp_path: Path) -> None:
    part = synth_part(state="timeout", stat=None, netlist=None, yosys_seconds=880.0,
                      message="the technology mapping step ran out of time")  # fmt: skip
    ctx, project = make(tmp_path, SynthRunner(outcome(worker_result(synth=part)), netlist=None))
    result = tool.synthesize(ctx, "chip")
    assert not result.ok and result.details["timed_out"] is True
    assert "ran out of time" in result.summary and "900 s" in result.summary
    assert load_ledger(project.dir).ledger.runs[-1].stage == "timeout"


def test_a_killed_container_is_reported_as_a_timeout(tmp_path: Path) -> None:
    ctx, _ = make(tmp_path, SynthRunner(outcome(None, timed_out=True, exit_code=137)))
    result = tool.synthesize(ctx, "chip")
    assert not result.ok and result.details["stage"] == "timeout"
    assert "ran out of time" in result.summary and "900 s" in result.summary


def test_a_lint_failure_stops_before_synthesis_and_says_so(tmp_path: Path) -> None:
    lint = [{"severity": "error", "code": "LATCH", "file": "src/chip.v", "line": 12, "col": 3,
             "msg": "Latch inferred for signal 'x'", "detail": ""}]  # fmt: skip
    result_json = worker_result(ok=False, stage="lint", lint=lint, lint_total=1, synth=None)
    ctx, project = make(tmp_path, SynthRunner(outcome(result_json)))
    result = tool.synthesize(ctx, "chip")
    assert not result.ok
    lines = result.summary.splitlines()
    assert lines[0].startswith("synthesize stopped before synthesis")
    assert any("src/chip.v:12:3: error LATCH" in ln for ln in lines)
    assert result.details["stage"] == "lint"
    assert all("emit_verilog" not in h for h in result.next_hints)
    assert not (project.dir / "build" / "synth").exists()


def test_a_python_syntax_error_fails_before_any_container_call(tmp_path: Path) -> None:
    runner = SynthRunner()
    ctx, project = make(tmp_path, runner, "amaranth")
    (project.dir / "hdl" / "chip.py").write_text("def broken(:\n")
    result = tool.synthesize(ctx, "chip")
    assert not result.ok and result.details["stage"] == "elaborate"
    assert runner.calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.update(synth={"state": "ran"}),  # no statistics
        lambda r: r["synth"].update(state="bogus"),
        lambda r: r["synth"].update(stat={**STAT, "cells": -1}),
        lambda r: r.update(synth=None),
    ],
)
def test_a_malformed_synth_result_is_unreadable_not_a_pass(tmp_path: Path, mutate: Any) -> None:
    data = worker_result()
    mutate(data)
    ctx, project = make(tmp_path, SynthRunner(outcome(data)))
    result = tool.synthesize(ctx, "chip")
    assert not result.ok
    assert not (project.dir / "build" / "synth" / "tt_um_chip.v").exists()


def test_a_missing_or_wrong_netlist_is_unreadable_not_a_pass(tmp_path: Path) -> None:
    ctx, _ = make(tmp_path, SynthRunner(outcome(worker_result()), netlist=None))
    assert not tool.synthesize(ctx, "chip").ok
    tmp2 = tmp_path / "two"
    tmp2.mkdir()
    ctx, _ = make(
        tmp2, SynthRunner(outcome(worker_result()), netlist="module other(); endmodule\n")
    )
    assert not tool.synthesize(ctx, "chip").ok


def test_a_run_is_recorded_and_status_shows_synthesis_pass_then_stale(tmp_path: Path) -> None:
    ctx, project = make(tmp_path, SynthRunner(outcome(worker_result())))
    assert tool.synthesize(ctx, "chip").ok
    record = load_ledger(project.dir).ledger.runs[-1]
    assert record.tool == "synthesize" and record.ok and record.stage == "synth"
    assert record.synth is not None
    assert (record.synth.cells, record.synth.flip_flops, record.synth.tiles) == (46, 8, "1x1")
    assert record.synth.netlist_sha256 == hashlib.sha256(NETLIST.encode()).hexdigest()

    def row(name: str) -> tuple[str, str]:
        report = compute_status(project, ctx.image)
        step = next(r for r in report.steps if r.key == name)
        return step.state, step.evidence

    assert row("synthesis")[0] == "pass"
    spec = project.dir / "spec.yaml"
    spec.write_text(spec.read_text().replace("clock_hz: 10000000", "clock_hz: 20000000"))
    from siliconsmith.tt_sync import sync_spec_files

    sync_spec_files(project)
    assert row("synthesis")[0] == "stale"


def test_the_tool_is_registered_with_a_plain_description() -> None:
    from siliconsmith.tools import TOOL_MODULES

    assert TOOL_MODULES.index("synthesize") < TOOL_MODULES.index("status")
    text = tool.DESCRIPTION
    for phrase in ("sky130", "ESTIMATE", "66 MHz", "bigger tile", "owner", "build/synth", "900 s"):
        assert phrase in text, phrase
    json.dumps(text)
