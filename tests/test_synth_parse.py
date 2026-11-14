# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Reading Yosys's synthesis reports: statistics, latches, `check` problems (real captures)."""

from __future__ import annotations

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import synth_parse as sp

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "synth"


def text(name: str) -> str:
    return (FIXTURES / name).read_text()


# -- statistics ----------------------------------------------------------------------------


def test_counter_statistics() -> None:
    stat = sp.parse_stat(text("counter_stat.json"))
    assert stat["cells"] == 46
    assert stat["flip_flops"] == 8
    assert stat["latch_cells"] == 0
    assert stat["combinational"] == 38
    assert stat["area_um2"] == pytest.approx(417.9008)
    assert stat["types"]["sky130_fd_sc_hd__dfxtp_2"] == 8


def test_top_cells_are_ranked_by_count_then_name() -> None:
    types = {"b": 3, "a": 3, "c": 9, "d": 1}
    assert sp.top_cells(types, 3) == [
        {"cell": "c", "count": 9},
        {"cell": "a", "count": 3},
        {"cell": "b", "count": 3},
    ]


def test_at_most_ten_top_cells_by_default() -> None:
    types = {f"cell{i:02d}": i + 1 for i in range(25)}
    assert len(sp.top_cells(types)) == 10


@pytest.mark.parametrize(
    ("cell", "flop", "latch"),
    [
        ("sky130_fd_sc_hd__dfxtp_2", True, False),
        ("sky130_fd_sc_hd__dfrtp_1", True, False),
        ("sky130_fd_sc_hd__dfbbp_1", True, False),
        ("sky130_fd_sc_hd__edfxtp_1", True, False),
        ("sky130_fd_sc_hd__dlxtp_1", False, True),
        ("sky130_fd_sc_hd__dlrtn_2", False, True),
        ("sky130_fd_sc_hd__dlclkp_1", False, False),  # a clock gate
        ("sky130_fd_sc_hd__dlygate4sd1_1", False, False),  # a delay buffer
        ("sky130_fd_sc_hd__nand2_2", False, False),
        ("sky130_fd_sc_hd__conb_1", False, False),
    ],
)
def test_cell_families(cell: str, flop: bool, latch: bool) -> None:
    assert sp.is_flip_flop(cell) is flop
    assert sp.is_latch(cell) is latch


def test_forbidden_cells_are_the_dont_use_lists_and_every_lpflow_cell() -> None:
    types = {
        "sky130_fd_sc_hd__nand2_2": 4,
        "sky130_fd_sc_hd__dfxtp_1": 2,  # on a do-not-use list
        "sky130_fd_sc_hd__lpflow_isobufsrc_1": 1,  # a low-power cell
        "sky130_fd_sc_hd__lpflow_new_cell_9": 1,  # not on any list, still an lpflow cell
    }
    found = sp.forbidden_cells(types, {"sky130_fd_sc_hd__dfxtp_1"})
    assert found == [
        "sky130_fd_sc_hd__dfxtp_1",
        "sky130_fd_sc_hd__lpflow_isobufsrc_1",
        "sky130_fd_sc_hd__lpflow_new_cell_9",
    ]
    assert sp.forbidden_cells({"sky130_fd_sc_hd__nand2_2": 1}, set()) == []


@pytest.mark.parametrize("bad", ["", "not json", "{}", '{"design": {"num_cells": "x"}}'])
def test_unreadable_statistics_raise(bad: str) -> None:
    with pytest.raises(sp.StatError):
        sp.parse_stat(bad)


@pytest.mark.parametrize("bad", ["", "not json", "{}", "[]"])
def test_unreadable_generic_statistics_raise(bad: str) -> None:
    with pytest.raises(sp.StatError):
        sp.estimate_generic_area(bad)


# -- the pre-abc estimate (calibration, see worker/synth.py) -------------------------------


@pytest.mark.parametrize("name", ["counter", "shift256", "rom256", "mult24"])
def test_generic_estimate_is_close_to_the_real_area(name: str) -> None:
    """The estimate is within 0.8x..1.2x of the mapped area on four real, different designs."""
    estimate, cells = sp.estimate_generic_area(text(f"{name}_generic.json"))
    real = sp.parse_stat(text(f"{name}_stat.json"))["area_um2"]
    assert cells > 0
    assert 0.8 <= estimate / real <= 1.2, (estimate, real)


def test_bookkeeping_cells_cost_nothing() -> None:
    doc = '{"design": {"num_cells_by_type": {"$scopeinfo": 3, "$_NOT_": 2}}}'
    area, cells = sp.estimate_generic_area(doc)
    assert (area, cells) == (2 * sp.GENERIC_AREA_UM2["$_NOT_"], 2)


def test_flip_flops_are_priced_apart_from_gates() -> None:
    doc = '{"design": {"num_cells_by_type": {"$_SDFFE_PN0P_": 10, "$_DFF_P_": 5, "$_XOR_": 1}}}'
    area, _ = sp.estimate_generic_area(doc)
    assert area == 15 * sp.FLIP_FLOP_AREA_UM2 + sp.GENERIC_AREA_UM2["$_XOR_"]


# -- latches -------------------------------------------------------------------------------


def test_latch_report_gives_signal_file_and_line() -> None:
    assert sp.parse_latches(text("latch.rpt"), "counter") == [
        {"signal": "held", "file": "src/counter.v", "line": 10}
    ]


def test_latch_report_with_no_latch_is_empty() -> None:
    assert sp.parse_latches("\n16. Executing PROC_DLATCH pass\n", "counter") == []


def test_latches_are_listed_once() -> None:
    line = text("latch.rpt")
    assert len(sp.parse_latches(line + line, "counter")) == 1


# -- check reports -------------------------------------------------------------------------


def checks(report: str, core: str = "") -> list[dict[str, object]]:
    return sp.parse_checks([text(report)], "counter", core)


def test_combinational_loop_has_a_source_line() -> None:
    (loop,) = checks("loop_pre_synth_chk.txt")
    assert loop["kind"] == "comb_loop"
    assert loop["file"] == "src/counter.v"
    assert loop["line"] in (10, 11)  # the two assigns that feed each other
    assert loop["signal"] == ""  # lint names the module here, which is not a signal
    assert "loop" in str(loop["message"])


def test_multiple_drivers_point_at_the_driving_line() -> None:
    (found,) = checks("multi_pre_synth_chk.txt")[:1]
    assert found["kind"] == "multiple_drivers"
    assert found["file"] == "src/counter.v"
    assert found["line"] == 9


def test_multiple_drivers_also_report_the_loop_yosys_finds_in_the_same_text() -> None:
    kinds = {item["kind"] for item in checks("multi_pre_synth_chk.txt")}
    assert kinds == {"multiple_drivers", "comb_loop"}


def test_undriven_net_is_found_in_the_core_text() -> None:
    (found,) = checks("undriven_pre_synth_chk.txt", core=text("undriven.v"))
    assert found["kind"] == "undriven"
    assert found["signal"] == "x"
    assert found["file"] == "src/counter.v"
    assert found["line"] == 8  # where `x` is declared
    assert "x" in str(found["message"]) and "nothing drives" in str(found["message"])


def test_undriven_output_is_named() -> None:
    (found,) = checks("undrv_out_pre_synth_chk.txt", core=text("undriven.v"))
    assert (found["kind"], found["signal"]) == ("undriven", "uo_out")
    assert found["file"] == "src/counter.v"
    assert found["line"] == 6  # the `output wire [7:0] uo_out` port declaration


def test_clean_report_has_no_problems() -> None:
    clean = "20. Executing CHECK pass\nChecking module a...\nFound and reported 0 problems.\n"
    assert sp.parse_checks([clean], "counter") == []


def test_the_same_problem_in_two_reports_is_listed_once() -> None:
    both = [text("undriven_pre_synth_chk.txt")] * 2
    assert len(sp.parse_checks(both, "counter", text("undriven.v"))) == 1


def test_project_file_names() -> None:
    assert sp.project_file("counter.v", "counter") == "src/counter.v"
    assert sp.project_file("project.v", "counter") == "src/project.v"
    assert sp.project_file("/work/hdl/x.py", "counter") == "hdl/x.py"
