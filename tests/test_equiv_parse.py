# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The readers of Yosys output for the equivalence check, pinned on saved real logs."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import equiv_parse as ep
from siliconsmith.worker import equiv_script as es

FIX = REPO_ROOT / "tests" / "fixtures" / "equiv"


def log(scenario: str, name: str) -> str:
    return (FIX / scenario / name).read_text()


# -- text guards -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "1'bx", ".A(1'bz)", "4'b01z0", "8'bxxxxxxxx", "2'b1?",
        # every other spelling of z (blanks, no size, signed, other bases, upper case)
        "'bz", "1'b z", "1 'bz", "'hz", "'z", "'x", "1'sb z", "1'B Z", "1'o z", "1'sbz",
        "4'd1x", "8'hzz", "1'b_z", "{1'b0,1'bz}",
    ],
)  # fmt: skip
def test_xz_constants_are_found(text: str) -> None:
    assert ep.xz_constant(f"cell u (\n  {text}\n);") is not None


@pytest.mark.parametrize(
    "text",
    [
        "1'b0",
        ".A(1'b1)",
        "8'b10100101",
        "wire x;",
        "xor2 x1 (.A(a))",
        "8'hFF",
        "'d5 , z1",
        "8'sd12",
    ],
)
def test_ordinary_constants_are_not_x(text: str) -> None:
    assert ep.xz_constant(text) is None


def test_show_bits() -> None:
    assert ep.show_bits("00001010") == "0x0a"
    assert ep.show_bits("1") == "1" and ep.show_bits("x") == "x"
    assert ep.show_bits("0x01") == "0b0x01"
    assert ep.show_bits(None) == "x"
    assert ep.show_bits("xxxxxxxx") == "0bxxxxxxxx"


# -- guards: the last marker in a failed log names the guard -----------------------------------


@pytest.mark.parametrize(
    ("scenario", "verdict", "reason"),
    [
        ("tristz", "not_proven", "guard_tristate"),
        ("async_m0229", "not_proven", "guard_async_set_reset"),
        ("clkinv", "not_proven", "guard_clocking"),
        ("loop", "error", "structure"),
        ("clockgate", "error", "cells"),
    ],
)
def test_guard_refusals_on_saved_logs(scenario: str, verdict: str, reason: str) -> None:
    got = ep.guard_failure(1, log(scenario, "equiv.log"))
    assert got is not None
    assert (got["verdict"], got["reason"]) == (verdict, reason)
    assert got["message"]


def test_the_clock_guard_names_the_cell_it_refused() -> None:
    got = ep.guard_failure(1, log("clkinv", "equiv.log"))
    assert got is not None and any("_42_.CLK" in line for line in got["detail"])
    assert "falling-edge flip-flop" in got["message"]


def test_the_async_guard_message_names_the_cell_family() -> None:
    got = ep.guard_failure(1, log("async_m0229", "equiv.log"))
    assert got is not None and "dfbb" in got["message"]


def test_a_loop_error_line_is_kept() -> None:
    got = ep.guard_failure(1, log("loop", "equiv.log"))
    assert got is not None and any("Found 1 problems" in line for line in got["detail"])


def test_a_cell_without_a_function_is_named() -> None:
    got = ep.guard_failure(1, log("clockgate", "equiv.log"))
    assert got is not None and "dlclkp_1" in got["message"]


def test_no_failure_when_yosys_succeeded() -> None:
    assert ep.guard_failure(0, "anything") is None


def test_a_killed_call_is_a_time_limit_not_a_pass() -> None:
    got = ep.guard_failure(None, "")
    assert got is not None
    assert (got["verdict"], got["reason"]) == ("not_proven", "time_limit")


def test_an_unmarked_failure_is_an_error() -> None:
    got = ep.guard_failure(1, "ERROR: syntax error in line 3\n")
    assert got is not None
    assert (got["verdict"], got["reason"]) == ("error", "yosys_failed")
    assert got["detail"] == ["ERROR: syntax error in line 3"]


# -- stage 1 -----------------------------------------------------------------------------------


def test_stage1_proven() -> None:
    got = ep.parse_stage1(log("counter_good", "equiv.log"))
    assert got == {"proven": 24, "unproven": 0, "unproven_signals": [], "unpaired_outputs": []}


def test_stage1_unproven_names_the_point() -> None:
    got = ep.parse_stage1(log("counter_m0115", "equiv.log"))
    assert got is not None
    assert (got["proven"], got["unproven"]) == (23, 1)
    assert got["unproven_signals"] == ["uo_out[5]"]
    assert got["unpaired_outputs"] == []  # paired by name, though one bit is not proven


def test_stage1_without_a_summary_is_none() -> None:
    assert ep.parse_stage1("nothing here") is None


def test_initial_values_are_seen_between_the_markers() -> None:
    assert ep.has_inits(log("initz", "equiv.log")) is True
    assert ep.has_inits(log("counter_good", "equiv.log")) is False
    assert ep.has_inits("INITS-BEGIN\nINITS-END\n") is False


# -- the stage-1 model ---------------------------------------------------------------------------


def test_equiv_points_of_the_counter() -> None:
    points, n_gold, n_gate = ep.equiv_points(log("counter_good", "equiv.json"))
    assert len(points) == 24
    assert (n_gold, n_gate) == (8, 8)
    labels = [p.label for p in points]
    assert sorted(labels) == sorted(set(labels))
    assert {f"uo_out[{i}]" for i in range(8)} <= set(labels)
    assert sum(1 for p in points if p.tie) == 8  # the eight counter bits are tied
    constants = [p for p in points if p.a.startswith("1'")]
    assert constants and all(p.a in ("1'0", "1'1") for p in constants)
    assert not any(p.tie for p in constants)  # a constant is not a register
    a_sides = [p.a for p in points if p.tie]
    assert len(a_sides) == len(set(a_sides))  # each RTL bit is tied to one flop only
    count = next(p for p in points if p.label == "uo_out[3]")
    assert count.a.endswith("[3]") or "count" in count.a


def test_base_model_adds_one_wire_pair_per_point_inside_the_module() -> None:
    il = log("counter_good", "equiv.il")
    points, _, _ = ep.equiv_points(log("counter_good", "equiv.json"))
    model = ep.base_model(il, points)
    assert model.count("  wire \\eqa_") == 24 and model.count("  wire \\eqb_") == 24
    assert "  connect \\eqa_0 " + points[0].a in model
    assert "  connect \\eqb_23 " + points[23].b in model
    assert model.rstrip().endswith("end")
    assert model.rfind("connect \\eqb_23") < model.rstrip().rfind("\nend")  # inside the module


# -- tables and counterexamples ------------------------------------------------------------------


def test_table_reads_steps_names_and_bits() -> None:
    vals = ep.table(log("counter_m0442", "base.log"))
    assert set(vals) == set(range(1, 8))  # the base case runs 7 cycles
    assert vals[1]["eqa_16"] == "x" and vals[1]["eqa_0"] == "0"
    assert vals[1]["eqb_0"] == "x"  # `--` columns, the bit string is `x`
    tab = ep.table(log("counter_m0115", "kind_x.log"))
    assert tab[1]["in_rst_n"] == "0" and tab[1]["in_ui_in"] == "00000001"
    assert tab[1]["gold_uo_out"] == "xxxxxxxx"
    assert tab[2]["gold_uo_out"] == "00000000" and tab[2]["in_rst_n"] == "1"


def test_base_case_with_a_difference_in_the_x_run() -> None:
    points, _, _ = ep.equiv_points(log("counter_m0442", "equiv.json"))
    got = ep.parse_base(log("counter_m0442", "base.log"), points)
    assert got["status"] == "cex" and got["run"] == "X" and got["cycle"] == 1
    assert got["outputs_differ"] is True
    assert {"signal": "uio_oe[0]", "rtl": "0", "netlist": "x"} in got["differs"]
    assert got["inputs"]["rst_n"] == "0"


def test_base_case_that_holds() -> None:
    points, _, _ = ep.equiv_points(log("counter_good", "equiv.json"))
    assert ep.parse_base(log("counter_good", "base.log"), points) == {"status": "ok"}


def test_the_def_run_skips_cycle_1() -> None:
    """The same real failing table, relabelled as the DEF run: its only difference is in cycle 1,
    which the DEF run does not count (it starts from defined values), so nothing is read."""
    points, _, _ = ep.equiv_points(log("counter_m0442", "equiv.json"))
    text = log("counter_m0442", "base.log").replace("BASE-X", "BASE-DEF", 1)
    text = text.split("BASE-DEF\n", 1)[1].split("BASE-DEF\n")[0]
    clean_x = "BASE-X\nSAT proof finished - no model found: SUCCESS!\n"
    got = ep.parse_base(clean_x + "BASE-DEF\n" + text, points)
    assert got["status"] == "unreadable" and got["run"] == "DEF"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "BASE-X\nSAT proof finished - no model found: SUCCESS!\n",  # no DEF run
        "BASE-DEF\nSAT proof finished - no model found: SUCCESS!\n",  # no X run
        "BASE-X\nSAT proof finished - no model found: SUCCESS!\nBASE-DEF\nrunning\n",
        "BASE-X\nSAT proof finished - no model found: SUCCESS!\nBASE-DEF\n"
        "SAT proof finished - no model found: SUCCESS!\nSAT proof finished - no model found: "
        "SUCCESS!\n",  # printed twice: not the output of one run
    ],
)
def test_a_base_case_log_without_a_clean_proof_in_both_runs_is_not_ok(text: str) -> None:
    points, _, _ = ep.equiv_points(log("counter_good", "equiv.json"))
    assert ep.parse_base(text, points)["status"] == "unreadable"


def test_the_state_check_result() -> None:
    assert ep.state_result(log("initstate_equal", "initstate.log")) == "equal"
    assert ep.state_result(log("initstate_deep", "initstate.log")) == "differ"
    assert ep.state_result("") == "unreadable"
    got = ep.parse_cex(log("initstate_deep", "initstate.log"))["first_mismatch"]
    assert got is not None and got["cycle"] == 2
    assert got["differs"] == [{"signal": "core.flag[0]", "rtl": "1", "netlist": "x"}]


def test_a_bounded_search_needs_its_positive_end_line() -> None:
    assert ep.reached_depth(log("counter_good", "bmc.log"), 32)
    assert not ep.reached_depth(log("counter_good", "bmc.log"), 31)
    assert not ep.reached_depth("", 32)


def test_counterexample_trace_and_first_mismatch() -> None:
    got = ep.parse_cex(log("counter_m0115", "kind_x.log"))
    trace, first = got["trace"], got["first_mismatch"]
    assert [row["cycle"] for row in trace][:2] == [1, 2]
    assert trace[0]["inputs"] == {
        "rst_n": "0",
        "ui_in": "00000001",
        "uio_in": trace[0]["inputs"]["uio_in"],
    }
    assert first is not None and first["cycle"] >= 2
    assert first["cycle"] == trace[-1]["cycle"]
    diff = first["differs"][0]
    assert (
        diff["signal"].startswith(("uo_out[", "uio_out[", "uio_oe["))
        and diff["rtl"] != diff["netlist"]
    )
    row = next(r for r in trace if r["cycle"] == first["cycle"])
    assert set(row["rtl"]) == {"uo_out", "uio_out", "uio_oe"} == set(row["netlist"])


def test_an_x_in_the_rtl_is_not_a_difference() -> None:
    """Cycle 1 of the same trace has an x output on the RTL side: never reported."""
    got = ep.parse_cex(log("counter_m0115", "kind_x.log"))
    assert got["first_mismatch"] is not None and got["first_mismatch"]["cycle"] != 1


def test_cycles_covered_and_induction_length() -> None:
    assert ep.covered_cycles(log("counter_m0043", "kind_zero.log")) == 32
    assert ep.induction_length(log("shiftnr_m0365", "kind_x.log")) == 16
    assert ep.covered_cycles("") == 0 and ep.induction_length("") is None


def test_the_strings_of_the_parsers_are_in_the_saved_logs() -> None:
    """Pin the Yosys 0.62 wording: if an upgrade changes it, this fails first."""
    assert "Of those cells 24 are proven and 0 are unproven" in log("counter_good", "equiv.log")
    assert "Unproven $equiv" in log("counter_m0115", "equiv.log")
    assert "Presumably equivalent wires: uo_out_gold" in log("counter_good", "equiv.log")
    assert "SAT proof finished - model found: FAIL!" in log("counter_m0442", "base.log")
    assert "SAT proof finished - no model found: SUCCESS!" in log("counter_good", "base.log")
    assert "model found for base case: FAIL" in log("counter_m0115", "kind_x.log")
    assert "Induction step proven: SUCCESS" in log("shiftnr_m0365", "kind_x.log")
    assert "Trying induction with length" in log("shiftnr_m0365", "kind_x.log")
    assert "Base case for induction length" in log("counter_m0043", "kind_x.log")
    assert "Selection contains:" in log("clkinv", "equiv.log")
    for marker in (
        es.M_CLOCKING,
        es.M_TRISTATE,
        es.M_ASYNC_SR,
        es.M_STRUCTURE,
        es.M_CELLS,
        es.M_OK,
    ):
        assert marker in log("counter_good", "equiv.log") or marker in log("clkinv", "equiv.log")
    assert es.INITS_BEGIN in log("initz", "equiv.log") and es.INITS_END in log("initz", "equiv.log")
    assert Path(FIX / "README.md").is_file()
