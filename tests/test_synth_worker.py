# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The worker's `synth` verb with a fake Yosys: early stop, results, failures, timeouts."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import emit, synth, synth_script

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "synth"


def request(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "chip",
        "frontend": "verilog",
        "author": "Ada",
        "run_dir": "/work/build/.ss/abc",
        "clock_period_ns": 100,
        "stop_area_um2": 29688.0,
        "timeout_s": 900,
    }
    base.update(fields)
    return base


class FakeYosys:
    """Stands in for `synth._yosys`: writes the reports a real run would leave."""

    def __init__(
        self,
        run_dir: Path,
        *,
        generic: str = "counter_generic.json",
        stat: str = "counter_stat.json",
        latch: str = "",
        returncode: int = 0,
        stderr: str = "",
        timeout_on: str | None = None,
        write_netlist: bool = True,
    ) -> None:
        self.reports = run_dir / "out" / "synth" / "reports"
        self.netlist = run_dir / "out" / "synth" / "tt_um_chip.v"
        self.generic, self.stat, self.latch = generic, stat, latch
        self.returncode, self.stderr = returncode, stderr
        self.timeout_on, self.write_netlist = timeout_on, write_netlist
        self.halves: list[str] = []
        self.timeouts: list[float] = []

    def __call__(
        self, script: Path, log: Path, cwd: Path, half: str, timeout_s: float
    ) -> subprocess.CompletedProcess[str]:
        self.halves.append(half)
        self.timeouts.append(timeout_s)
        if half == self.timeout_on:
            raise synth._Timeout(half)
        if self.returncode == 0 and half == "front":
            (self.reports / "generic.json").write_text((FIXTURES / self.generic).read_text())
            (self.reports / "latch.rpt").write_text(self.latch)
            (self.reports / "pre_synth_chk.rpt").write_text("Found and reported 0 problems.\n")
        if self.returncode == 0 and half == "back":
            (self.reports / "stat.json").write_text((FIXTURES / self.stat).read_text())
            (self.reports / "chk.rpt").write_text("Found and reported 0 problems.\n")
            if self.write_netlist:
                self.netlist.write_text("module tt_um_chip(); endmodule\n")
        return subprocess.CompletedProcess(
            [], self.returncode, "", self.stderr if self.returncode else ""
        )


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "chip.v").write_text("module chip(); endmodule\n")
    lists = tmp_path / "lists"
    lists.mkdir()
    (lists / "no_synth.cells").write_text("sky130_fd_sc_hd__clkbuf_1\nsky130_fd_sc_hd__dfxtp_1\n")
    (lists / "drc_exclude.cells").write_text("sky130_fd_sc_hd__ha_1\n")
    monkeypatch.setattr(synth_script, "NO_SYNTH_CELLS", str(lists / "no_synth.cells"))
    monkeypatch.setattr(synth_script, "DRC_EXCLUDE_CELLS", str(lists / "drc_exclude.cells"))
    return tmp_path


def go(
    run_dir: Path, fake: FakeYosys, monkeypatch: pytest.MonkeyPatch, **kw: Any
) -> dict[str, Any]:
    monkeypatch.setattr(synth, "_yosys", fake)
    args: dict[str, Any] = {
        "name": "chip",
        "frontend": "verilog",
        "run_dir": run_dir,
        "clock_period_ns": 100.0,
        "stop_area_um2": 29688.0,
        "deadline": time.monotonic() + 600,
    }
    args.update(kw)
    return synth.synthesize(
        args["name"],
        args["frontend"],
        args["run_dir"],
        args["clock_period_ns"],
        args["stop_area_um2"],
        args["deadline"],
    )


def test_a_design_that_fits_runs_both_halves(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeYosys(run_dir)
    result = go(run_dir, fake, monkeypatch)
    assert fake.halves == ["front", "back"]
    assert result["state"] == "ran"
    assert result["stat"] == {
        "cells": 46,
        "flip_flops": 8,
        "latch_cells": 0,
        "combinational": 38,
        "area_um2": pytest.approx(417.9008),
    }
    assert result["top_cells"][0] == {"cell": "sky130_fd_sc_hd__conb_1", "count": 16}
    assert len(result["top_cells"]) == 10
    assert result["forbidden_cells"] == []
    assert result["latches"] == [] and result["checks"] == []
    assert result["netlist"] == "synth/tt_um_chip.v"
    assert len(result["netlist_sha256"]) == 64
    assert result["estimate"]["generic_cells"] == 24  # the $scopeinfo cell is not counted


def test_the_scripts_and_helper_files_are_written_before_yosys_runs(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    go(run_dir, FakeYosys(run_dir), monkeypatch)
    sd = run_dir / "out" / "synth"
    back = (sd / "back.ys").read_text()
    assert "-dont_use sky130_fd_sc_hd__clkbuf_1" in back
    assert "-dont_use sky130_fd_sc_hd__ha_1" in back  # the second list too
    assert "-D 100000 " in back
    assert (sd / "area0.abc").read_text() == synth_script.abc_area0_script()
    assert "read_verilog -defer -noautowire -sv" in (sd / "front.ys").read_text()


def test_early_stop_never_starts_the_technology_mapping(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeYosys(run_dir, generic="mult24_generic.json")
    result = go(run_dir, fake, monkeypatch, stop_area_um2=10_000.0)
    assert fake.halves == ["front"]
    assert result["state"] == "too_big"
    assert result["stat"] is None and result["netlist"] is None
    assert result["estimate"]["generic_area_um2"] > 10_000
    assert result["estimate"]["stop_area_um2"] == 10_000.0


def test_a_design_just_under_the_limit_is_not_stopped(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probe = FakeYosys(run_dir, generic="mult24_generic.json")
    area = go(run_dir, probe, monkeypatch, stop_area_um2=1.0)["estimate"]["generic_area_um2"]
    fake = FakeYosys(run_dir, generic="mult24_generic.json", stat="mult24_stat.json")
    assert go(run_dir, fake, monkeypatch, stop_area_um2=area + 1)["state"] == "ran"
    assert go(run_dir, fake, monkeypatch, stop_area_um2=area - 1)["state"] == "too_big"


def test_a_yosys_error_gives_the_error_lines_with_project_paths(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stderr = f"{run_dir}/out/chip.v:3: ERROR: syntax error, unexpected '}}'\n"
    fake = FakeYosys(run_dir, returncode=1, stderr=stderr)
    result = go(run_dir, fake, monkeypatch)
    assert fake.halves == ["front"]
    assert result["state"] == "yosys_failed"
    assert result["log"] == ["src/chip.v:3: ERROR: syntax error, unexpected '}'"]


def test_a_timeout_names_the_half_that_ran_out_of_time(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = go(run_dir, FakeYosys(run_dir, timeout_on="back"), monkeypatch)
    assert result["state"] == "timeout"
    assert "technology mapping" in result["message"]
    result = go(run_dir, FakeYosys(run_dir, timeout_on="front"), monkeypatch)
    assert result["state"] == "timeout" and "generic synthesis" in result["message"]


def test_yosys_gets_the_time_that_is_left(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeYosys(run_dir)
    go(run_dir, fake, monkeypatch, deadline=time.monotonic() + 100)
    assert 0 < fake.timeouts[1] <= fake.timeouts[0] <= 100


def test_a_missing_netlist_is_a_failure_not_a_pass(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = go(run_dir, FakeYosys(run_dir, write_netlist=False), monkeypatch)
    assert result["state"] == "yosys_failed" and "no netlist" in result["message"]


def test_do_not_use_and_lpflow_cells_are_reported(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    text = (FIXTURES / "counter_stat.json").read_text().replace(
        "sky130_fd_sc_hd__dfxtp_2", "sky130_fd_sc_hd__dfxtp_1"
    ).replace("sky130_fd_sc_hd__inv_2", "sky130_fd_sc_hd__lpflow_isobufsrc_1")  # fmt: skip
    (run_dir / "bad_stat.json").write_text(text)
    fake = FakeYosys(run_dir, stat=str(run_dir / "bad_stat.json"))
    result = go(run_dir, fake, monkeypatch)
    assert result["forbidden_cells"] == [
        "sky130_fd_sc_hd__dfxtp_1",
        "sky130_fd_sc_hd__lpflow_isobufsrc_1",
    ]


def test_a_latch_is_reported_with_file_and_line(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    latch = (FIXTURES / "latch.rpt").read_text().replace("counter.", "chip.").replace(
        "counter.v", "chip.v"
    )  # fmt: skip
    result = go(run_dir, FakeYosys(run_dir, latch=latch), monkeypatch)
    assert result["latches"] == [{"signal": "held", "file": "src/chip.v", "line": 10}]


def test_a_latch_cell_without_a_report_line_is_still_reported(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (FIXTURES / "counter_stat.json").read_text().replace("dfxtp_2", "dlxtp_1")
    (run_dir / "latch_stat.json").write_text(text)
    result = go(run_dir, FakeYosys(run_dir, stat=str(run_dir / "latch_stat.json")), monkeypatch)
    assert result["stat"]["latch_cells"] == 8 and result["stat"]["flip_flops"] == 0
    assert result["latches"] == [{"signal": "", "file": "", "line": 0}]


# -- the verb ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"clock_period_ns": 0},
        {"clock_period_ns": "100"},
        {"stop_area_um2": -1},
        {"timeout_s": None},
        {"timeout_s": True},
    ],
)
def test_bad_requests(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        synth.run(request(**bad))


def test_emit_failure_stops_before_yosys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": False, "stage": "lint"})
    monkeypatch.setattr(synth, "synthesize", lambda *a: pytest.fail("must not synthesize"))
    assert synth.run(request()) == {"ok": False, "stage": "lint", "synth": None}


def test_emit_success_runs_yosys_with_the_requests_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": True, "stage": "done"})
    seen: list[tuple[Any, ...]] = []

    def fake(*args: Any) -> dict[str, Any]:
        seen.append(args)
        return {"state": "ran", "message": "x" * 5000}

    monkeypatch.setattr(synth, "synthesize", fake)
    result = synth.run(request(frontend="myhdl", clock_period_ns=20, stop_area_um2=5.5))
    name, frontend, run_dir, period, stop, deadline = seen[0]
    assert (name, frontend, run_dir, period, stop) == (
        "chip",
        "myhdl",
        Path("/work/build/.ss/abc"),
        20.0,
        5.5,
    )
    assert deadline <= time.monotonic() + 900  # the host's limit, less the reserve
    assert len(result["synth"]["message"]) <= 2000
