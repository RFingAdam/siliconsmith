# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

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


@pytest.mark.parametrize(
    ("scenario", "name", "verdict", "reason"),
    [
        ("modules_macro", "equiv.log", "error", "netlist_modules"),  # a `define-hidden module
        ("cells_shx", "equiv.log", "error", "cells"),  # a $shiftx left in the netlist
        ("loop", "equiv.log", "error", "structure"),
    ],
)
def test_the_netlist_guards_name_what_they_refused_on_real_logs(
    scenario: str, name: str, verdict: str, reason: str
) -> None:
    """The logs are Yosys's own (trimmed to the failing guard), not made up."""
    got = ep.guard_failure(1, log(scenario, name))
    assert got is not None and (got["verdict"], got["reason"]) == (verdict, reason)
    assert any("Assertion" in x or "Found" in x for x in got["detail"]) or reason == "structure"


def test_a_state_check_that_could_not_expose_every_register_is_unusable() -> None:
    assert ep.state_result(log("expose_fail", "initstate.log")) == "unusable"


def test_the_rtl_memories_are_listed_between_markers() -> None:
    assert ep.has_memories("MEMS-BEGIN\ntt_um_c/mem\nMEMS-END\n") is True
    assert ep.has_memories("MEMS-BEGIN\nMEMS-END\n") is False
    assert ep.has_memories("nothing\n") is False


def test_include_is_found() -> None:
    assert ep.include_directive('`include "other.v"\n')
    assert ep.include_directive("`  include <x.vh>\n")
    assert not ep.include_directive("// include the file\n")


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("module tt_um_c(a);\nendmodule\n", None),
        ("module \\tt_um_c (a);\nendmodule\n", None),
        (
            "module tt_um_c(a);\nendmodule\nmodule sky130_fd_sc_hd__dlclkp_1(a);\nendmodule\n",
            "module",
        ),
        ('(* src = "x.v:1" *)\nmodule tt_um_c(a);\nendmodule\n', "src"),
        ('(* keep, src = "x.v:1" *)\nmodule tt_um_c(a);\nendmodule\n', "src"),
        ('`include "other.v"\nmodule tt_um_c(a);\nendmodule\n', "include"),
        ("macromodule other(a);\nendmodule\n", "module"),
    ],
)
def test_the_netlist_text_may_hold_the_top_module_only(text: str, found: str | None) -> None:
    got = ep.netlist_text_problem(text, "tt_um_c")
    assert (got is None) if found is None else (got is not None and found in got)


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
    points, n_gold, n_gate = ep.equiv_points(log("counter_good", "equiv.json"))
    model, wires = ep.base_model(il, points)
    equiv, _ = model.split(f"module \\{ep.X_TOP}\n")
    # equiv.il has eight ports; every point wire and power-up wire is an output port after them
    assert equiv.count("  wire output ") == 48
    assert "  wire output 9 \\eqa_0\n  wire output 10 \\eqb_0\n" in equiv
    assert "  wire output 56 \\eqb_23\n" in equiv
    assert "  connect \\eqa_0 " + points[0].a in equiv
    assert "  connect \\eqb_23 " + points[23].b in equiv
    assert equiv.rstrip().endswith("end")
    assert equiv.rfind("connect \\eqb_23") < equiv.rstrip().rfind("\nend")  # inside the module
    # One wire per side that holds the output of every flip-flop of that side.
    assert wires == {"gold": "ff_gold", "gate": "ff_gate"}
    flops = ep.flip_flops(il, ep.side_by_suffix)
    assert sum(w for _, w in flops["gold"]) == 8 and sum(w for _, w in flops["gate"]) == 8
    assert "  wire width 8 output 57 \\ff_gold\n  connect \\ff_gold { " in equiv
    assert "  wire width 8 output 58 \\ff_gate\n  connect \\ff_gate { " in equiv
    assert equiv.rfind("connect \\ff_gate") < equiv.rstrip().rfind("\nend")
    assert n_gold and n_gate


def test_the_x_run_reads_each_side_from_its_own_copy() -> None:
    """Round 15 n40: the X run's top module takes the RTL points and RTL power-up wire from the
    copy of `equiv` that keeps the RTL's values (`X_RTL`), the netlist's from the copy that keeps
    the netlist's (`X_NETLIST`): a netlist gate never reads an RTL register's x there."""
    il = log("counter_good", "equiv.il")
    points, _, _ = ep.equiv_points(log("counter_good", "equiv.json"))
    model, _ = ep.base_model(il, points)
    x_top = model.split(f"module \\{ep.X_TOP}\n")[1]
    assert model.count("\nmodule ") == 2 and x_top.endswith("end\n")  # equiv, then X_TOP
    head, rtl, net = x_top.split("  cell ")
    # the inputs of equiv.il, then every point wire and power-up wire as an output port
    assert head.startswith(
        "  wire input 1 \\clk\n  wire input 2 \\ena\n  wire input 3 \\rst_n\n"
        "  wire width 8 input 4 \\ui_in\n  wire width 8 input 5 \\uio_in\n"
        "  wire output 6 \\eqa_0\n"
    )
    assert "  wire width 8 output 30 \\ff_gold\n" in head
    assert head.endswith("  wire output 54 \\eqb_23\n  wire width 8 output 55 \\ff_gate\n")
    assert rtl.startswith(f"\\{ep.X_RTL} \\rtl\n    connect \\clk \\clk\n")
    assert net.startswith(f"\\{ep.X_NETLIST} \\net\n    connect \\clk \\clk\n")
    assert "connect \\eqa_23 \\eqa_23" in rtl and "eqb_" not in rtl and "ff_gate" not in rtl
    assert "connect \\ff_gold \\ff_gold" in rtl
    assert "connect \\eqb_23 \\eqb_23" in net and "eqa_" not in net and "ff_gold" not in net
    assert "connect \\ff_gate \\ff_gate" in net
    for name in ("\\ena", "\\rst_n", "\\ui_in", "\\uio_in"):
        assert f"    connect {name} {name}\n" in rtl and f"    connect {name} {name}\n" in net


@pytest.mark.parametrize(
    ("change", "why"),
    [
        (("wire width 8 input 2 \\ui_in", "wire width 8 inout 2 \\ui_in"), "cannot be read"),
        (("wire width 8 input 2 \\ui_in", "wire width 8 signed input 2 \\ui_in"), "plain"),
        (("wire width 8 input 2 \\ui_in", "wire width 8 offset 1 input 2 \\ui_in"), "plain"),
    ],
)
def test_an_input_the_x_run_cannot_copy_raises(change: tuple[str, str], why: str) -> None:
    il = log("counter_good", "equiv.il")
    assert change[0] in il
    with pytest.raises(ValueError, match=why):
        ep.base_model(il.replace(*change), [])


def test_a_power_up_wire_never_takes_a_name_the_model_already_has() -> None:
    il = log("counter_good", "equiv.il").replace(
        "module \\equiv\n", "module \\equiv\n  wire \\ff_gold\n  wire width 3 \\ff_gold_1\n", 1
    )
    _, wires = ep.base_model(il, [])
    assert wires == {"gold": "ff_gold_2", "gate": "ff_gate"}


def test_sides_are_read_from_the_cell_names() -> None:
    assert ep.side_by_suffix("$procdff$16_gold") == "gold"
    assert ep.side_by_suffix("\\_093__gate") == "gate"
    assert ep.side_by_suffix("$procdff$16") is None
    assert ep.side_by_prefix("$flatten\\gold.\\core.$procdff$16") == "gold"
    assert ep.side_by_prefix("\\gold.core.cnt_reg") == "gold"
    assert ep.side_by_prefix("$flatten\\gate.\\_093_.$auto$liberty.cc:255:create_ff$1519") == "gate"
    assert ep.side_by_prefix("\\gold") is None  # a scope cell of the instance itself
    assert ep.side_by_prefix("\\golden.x") is None and ep.side_by_prefix("$auto$x") is None


def test_the_miter_model_gets_the_power_up_wires_of_both_sides() -> None:
    il = (
        "module \\miter\n  wire width 2 \\gold.r\n  wire \\gate.q\n"
        "  cell $dff $flatten\\gold.$procdff$1\n    parameter \\WIDTH 2\n"
        "    connect \\Q \\gold.r\n  end\n"
        "  cell $_DFF_P_ $flatten\\gate.\\_1_.$auto$ff\n    connect \\Q \\gate.q\n  end\n"
        "end\n"
    )
    model, wires = ep.miter_model(il)
    assert wires == {"gold": "ff_gold", "gate": "ff_gate"}
    assert "  wire width 2 \\ff_gold\n  connect \\ff_gold { \\gold.r }" in model
    assert "  wire width 1 \\ff_gate\n  connect \\ff_gate { \\gate.q }\nend\n" in model
    # A side without flip-flops gets no wire (and no power-up option).
    _, only_gate = ep.miter_model(il.replace("$dff", "$and"))
    assert only_gate == {"gate": "ff_gate"}


@pytest.mark.parametrize(
    "bad",
    [
        "  cell $dlatch \\gold.l\n  end\n",
        "  cell $aldff \\gold.l\n  end\n",
        "  cell $_SDFF_PP0_ \\gate.s\n  end\n",
        "  cell $mem_v2 \\gold.m\n  end\n",
        "  cell $anyinit \\gold.a\n  end\n",
        "  cell $_FF_ \\gold.f\n  end\n",
        "  cell $_DFF_P_ \\other.f\n    connect \\Q \\x\n  end\n",  # neither side
        "  cell $_DFF_P_ \\gate.f\n  end\n",  # no Q
        "  cell $dff \\gold.f\n    connect \\Q \\x\n  end\n",  # no width
        "  attribute \\init 1'1\n  wire \\gold.w\n",  # an initial value
    ],
)
def test_a_state_whose_power_up_cannot_be_set_raises(bad: str) -> None:
    with pytest.raises(ValueError):
        ep.miter_model(f"module \\miter\n{bad}end\n")


def test_more_than_one_module_raises() -> None:
    with pytest.raises(ValueError):
        ep.miter_model("module \\miter\nend\nmodule \\gold\nend\n")


# -- tables and counterexamples ------------------------------------------------------------------


def test_table_reads_steps_names_and_bits() -> None:
    vals = ep.table(log("counter_m0442", "base.log"))
    assert set(vals) == set(range(1, 8))  # the base case runs 7 cycles
    assert vals[1]["eqa_16"] == "x"  # `--` columns, the bit string is `x`
    assert vals[1]["eqa_0"] == "0" and vals[1]["eqb_0"] == "1"
    tab = ep.table(log("counter_m0115", "kind_x.log"))
    assert tab[1]["in_rst_n"] == "0" and tab[1]["in_ui_in"] == "00000001"
    assert tab[1]["gold_uo_out"] == "xxxxxxxx"
    assert tab[2]["gold_uo_out"] == "00000000" and tab[2]["in_rst_n"] == "1"


def test_base_case_with_a_difference_in_the_x_run() -> None:
    points, _, _ = ep.equiv_points(log("counter_m0442", "equiv.json"))
    got = ep.parse_base(log("counter_m0442", "base.log"), points)
    assert got["status"] == "cex" and got["run"] == "X" and got["cycle"] == 1
    assert got["outputs_differ"] is True
    assert {"signal": "uio_oe[0]", "rtl": "0", "netlist": "1"} in got["differs"]
    assert got["inputs"]["rst_n"] == "0"


def test_the_x_run_shows_a_defined_netlist_value_at_every_point_and_cycle() -> None:
    """Round 15 n40: the X run reads the netlist side on its own (`base_model`), with every
    netlist flip-flop defined and every input defined, so no netlist value can be x. The old
    model spread an RTL register's x through the netlist gates and showed `netlist x` here."""
    x_run = log("counter_m0442", "base.log").split("BASE-DEF")[0]
    vals = ep.table(x_run)
    assert set(vals) == set(range(1, 8))
    netlist = [v for row in vals.values() for k, v in row.items() if k.startswith("eqb_")]
    assert len(netlist) == 7 * 24 and set(netlist) <= {"0", "1"}
    assert any(v == "x" for row in vals.values() for k, v in row.items() if k.startswith("eqa_"))


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


# -- the one-shot search of stage 4 (es.miter_once) ----------------------------------------------

WIRES = {"gold": "ff_gold", "gate": "ff_gate"}


@pytest.mark.parametrize("scenario", ["counter_good", "seg7reg"])
def test_a_one_shot_search_is_proved_only_with_its_whole_shape(scenario: str) -> None:
    """seg7reg: the registered decoder whose step-by-step search stalls (its `bmc.log` was cut
    off at 20 s after 18 cycles); the one-shot proves all 32 in about a second."""
    text = log(scenario, "bmc_once.log")
    assert ep.once_result(0, text, WIRES, 32) == "proved"
    assert ep.once_result(0, text, WIRES, 31) == "unclear"  # another depth than asked for
    assert ep.once_result(0, text, WIRES, 33) == "unclear"
    assert ep.covered_cycles(log("seg7reg", "bmc.log")) == 18


@pytest.mark.parametrize("code", [None, 1, 134])
def test_a_one_shot_search_without_exit_code_0_is_never_proved(code: int | None) -> None:
    assert ep.once_result(code, log("counter_good", "bmc_once.log"), WIRES, 32) == "unclear"
    assert ep.once_result(code, log("counter_swap", "bmc_once.log"), WIRES, 32) == "unclear"


def drop_lines(text: str, line: str) -> str:
    kept = [x for x in text.splitlines() if x != line]
    assert len(kept) < len(text.splitlines()), line
    return "\n".join(kept) + "\n"


@pytest.mark.parametrize(
    "missing",
    [
        "Import all_undef constraint for this timestep: \\ff_gold",  # the RTL's x power-up
        "Import def constraint for this timestep: \\ff_gate",  # the netlist's defined power-up
        "Import set-constraint for this timestep: \\in_rst_n = 1'0",  # reset at cycle 1
        "Setting up time step 33:",  # the last cycle
        "SAT proof finished - no model found: SUCCESS!",
    ],
)
def test_a_one_shot_search_whose_log_misses_a_constraint_is_not_proved(missing: str) -> None:
    text = drop_lines(log("counter_good", "bmc_once.log"), missing)
    assert ep.once_result(0, text, WIRES, 32) == "unclear"


def test_a_one_shot_search_needs_every_proof_step_and_no_more() -> None:
    text = log("counter_good", "bmc_once.log")
    assert text.count(ep.ONCE_PROOF) == 32  # time steps 2 to 33
    one_less = text.replace(ep.ONCE_PROOF + "\n", "", 1)
    assert ep.once_result(0, one_less, WIRES, 32) == "unclear"
    twice = text + ep.ONCE_PROVED + "\n"
    assert ep.once_result(0, twice, WIRES, 32) == "unclear"
    beyond = text + "Setting up time step 34:\n"
    assert ep.once_result(0, beyond, WIRES, 32) == "unclear"
    assert ep.once_result(0, text[: len(text) // 2], WIRES, 32) == "unclear"  # cut off
    assert ep.once_result(0, "", WIRES, 32) == "unclear"
    # a wire the log does not name (another power-up wire than the one asked for)
    assert ep.once_result(0, text, {"gold": "ff_gold_1", "gate": "ff_gate"}, 32) == "unclear"
    # a side without flip-flops has no power-up wire: nothing to look for there
    gate_only = drop_lines(text, "Import all_undef constraint for this timestep: \\ff_gold")
    assert ep.once_result(0, gate_only, {"gate": "ff_gate"}, 32) == "proved"


def test_a_one_shot_difference_is_read_like_any_counterexample() -> None:
    """counter_swap: the counter's netlist with one cell swapped (nand3 -> nor3). The one-shot
    model and the step-by-step search agree on the first differing cycle and bit; the one-shot
    trace runs to the last cycle (33), with other inputs than the step-by-step one."""
    text = log("counter_swap", "bmc_once.log")
    assert ep.once_result(0, text, WIRES, 32) == "cex"
    assert "SAT proof finished - model found: FAIL!" in text
    once, step = ep.parse_cex(text), ep.parse_cex(log("counter_swap", "bmc.log"))
    assert once["first_mismatch"] == step["first_mismatch"]
    assert once["first_mismatch"] == {
        "cycle": 4,
        "differs": [{"signal": "uo_out[1]", "rtl": "1", "netlist": "0"}],
    }
    assert len(once["trace"]) == 33 and len(step["trace"]) == 4
    assert once["trace"][0]["inputs"] != step["trace"][0]["inputs"]


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
