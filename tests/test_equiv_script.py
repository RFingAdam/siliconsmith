# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The Yosys scripts of the equivalence check: the strings the parser and the proof depend on."""

from __future__ import annotations

import pytest

from siliconsmith.worker import equiv_script as es
from siliconsmith.worker import synth_script


def lines(script: str) -> list[str]:
    return [ln for ln in script.splitlines() if ln.strip()]


def front() -> list[str]:
    return lines(synth_script.front_script(["project.v", "chip.v"], "tt_um_chip", "synth"))


def contiguous(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i : i + n] == needle for i in range(len(haystack) - n + 1))


# -- the RTL side repeats the production synthesis script ----------------------------------------


def test_production_script_still_has_the_passes_the_rtl_side_repeats() -> None:
    """Fails when synth_script changes its fsm or memory options and equiv_script does not."""
    prod = front()
    assert contiguous(prod, ["flatten", "opt_expr", "opt_clean", *es.PRODUCTION_FSM_PASSES])
    assert sum(1 for ln in prod if ln.split()[0] == "fsm") == 1  # one fsm pass, with no options
    memory = [ln for ln in prod if ln.split()[0].startswith("memory")]
    assert tuple(memory) == es.PRODUCTION_MEMORY_PASSES
    assert prod.index("proc_dff") < prod.index("flatten") < prod.index("fsm")
    assert "techmap" in prod and prod.index("memory_map") < prod.index("techmap")


def test_gold_repeats_the_production_passes_in_order() -> None:
    gold = lines(es.gold("chip", "proof"))
    assert contiguous(gold, ["flatten", es.DELETE_NON_HARDWARE, "opt_expr", "opt_clean"])
    assert contiguous(gold, [*es.PRODUCTION_FSM_PASSES, "opt_clean", "memory"])
    assert gold.index("memory") < gold.index("techmap t:$pmux") < gold.index("attrmap -remove init")


def test_what_the_production_script_removes_is_removed_from_the_rtl() -> None:
    back = lines(synth_script.back_script("tt_um_chip", "synth", 100.0, ["c"]))
    deleted = {ln.split()[1] for ln in [*front(), *back] if ln.startswith("delete ")}
    assert deleted == {"t:$print", "t:$assert"}
    assert deleted <= set(es.DELETE_NON_HARDWARE.split())
    assert "tribuf" in front()


def test_the_rtl_is_read_the_way_the_production_script_reads_it() -> None:
    read = lines(es.gold("chip", "proof"))[0]
    prod = next(ln for ln in front() if ln.startswith("read_verilog"))
    for flag in ("-defer", "-noautowire", "-sv"):
        assert flag in read and flag in prod
    for define in synth_script.DEFINES:
        assert f"-D{define}" in read


# -- the modes of the RTL side -------------------------------------------------------------------


def test_gold_modes() -> None:
    proof = es.gold("chip", "proof")
    assert "log INITS-BEGIN\nselect -list a:init\nlog INITS-END" in proof
    assert "attrmap -remove init" in proof and "fsm" in lines(proof)
    rtl = es.gold("chip", "rtl")
    assert "attrmap -remove init" not in rtl and "fsm" not in lines(rtl)
    assert "techmap t:$pmux" not in rtl and "memory" in lines(rtl)
    noinit = es.gold("chip", "rtl_noinit")
    assert "attrmap -remove init" in noinit and "INITS" not in noinit
    for script in (proof, rtl, noinit):  # what every mode ends with
        tail = lines(script)
        assert "check -assert" in tail and "async2sync" in tail and "dffunmap" in tail
        assert tail.index("GUARD-TRISTATE" if False else "log GUARD-TRISTATE") < tail.index(
            "check -assert"
        )
        assert tail[-1] == f"log {es.M_OK}"
    with pytest.raises(ValueError):
        es.gold("chip", "other")


def test_gate_reads_the_netlist_file_and_has_every_guard_in_order() -> None:
    gate = lines(es.gate("chip", "netlist.v"))
    # the netlist is read FIRST: every module that exists then is one the file defined
    assert gate[0] == "read_verilog netlist.v"
    assert gate[1] == f"log {es.M_MODULES}"
    assert gate[2] == "select -assert-none * tt_um_chip %d"  # nothing but the top, no `src` use
    assert "A:src" not in "\n".join(gate)
    assert gate[3].startswith("read_liberty -ignore_miss_func ")
    marks = [ln.removeprefix("log ") for ln in gate if ln.startswith("log GUARD")]
    assert marks == [
        es.M_MODULES,
        es.M_STRUCTURE,
        es.M_CELLS,
        es.M_TRISTATE,
        es.M_ASYNC_SR,
        es.M_CLOCKING,
        es.M_OK,
    ]
    assert "setundef -undriven -anyseq" in gate  # x and z constants become free values
    # An undriven net (a cell input left open) is refused BEFORE setundef makes it a free value
    assert gate.index("check -assert") < gate.index("setundef -undriven -anyseq")
    # the netlist as Yosys read it is written before anything changes it (x/z are looked for there)
    assert f"write_rtlil {es.GATE_RTLIL}" in gate
    assert gate.index("hierarchy -check -top tt_um_chip") < gate.index(
        f"write_rtlil {es.GATE_RTLIL}"
    )
    assert gate.index(f"write_rtlil {es.GATE_RTLIL}") < gate.index("flatten")
    # init attributes of the netlist are dropped before anything is proven
    assert "attrmap -remove init" in gate
    assert gate.index("flatten") < gate.index("attrmap -remove init")
    assert gate.index("attrmap -remove init") < gate.index("setundef -undriven -anyseq")
    # after flatten only plain gates (and $scopeinfo) may be left: no $shiftx, $div, ...
    assert "select -assert-none t:* t:$_* %d t:$scopeinfo %d" in gate
    assert gate.index("flatten") < gate.index("select -assert-none t:* t:$_* %d t:$scopeinfo %d")
    assert gate.index("read_verilog netlist.v") < gate.index("select -assert-none * tt_um_chip %d")
    assert gate.index("select -assert-none * tt_um_chip %d") < gate.index(
        "hierarchy -check -top tt_um_chip"
    )
    assert gate.index("hierarchy -check -top tt_um_chip") < gate.index("flatten")
    assert "select -assert-none t:$tribuf t:$_TBUF_" in gate
    assert any(ln.startswith("select -assert-none t:$dffsr t:$dffsre") for ln in gate)


def test_the_state_check_compares_every_register_at_cycle_2_without_a_cycle_limit() -> None:
    script = es.state_check("chip")
    rows = lines(script)
    assert rows.count("expose -dff") == 2  # the registers of both copies are outputs
    sat = next(ln for ln in rows if ln.startswith("sat "))
    assert "-seq 2" in sat and "-prove-skip 1" in sat  # the state after the one reset cycle
    assert "-set-init-undef" in sat and "-set-at 1 in_rst_n 0" in sat
    assert "-maxsteps" not in sat and "-tempinduct" not in sat  # no bound to run out of
    assert "-ignore_gold_x" in script and "-enable_undef" in sat
    # the first copy keeps the initial values, the second has none
    assert script.index("attrmap -remove init") > script.index("design -stash gold")


def test_the_state_check_exposes_every_flop_before_async2sync_and_fails_closed() -> None:
    """async2sync moves the output of a flop with an asynchronous reset to an internal wire that
    `expose -dff` skips: exposing afterwards missed such registers (and their initial values)."""
    rows = lines(es.state_check("chip"))
    assert rows.count("async2sync") == 2 and rows.count("expose -dff") == 2
    first = rows.index("expose -dff")
    assert rows.index("async2sync", first - 5) > first  # exposed first, then async2sync
    assert rows[first - 1] == f"log {es.M_EXPOSE}"
    # fail closed: every flip-flop output must be a port now, else the state check is unusable
    assert rows[first + 1] == (
        "select -assert-none t:$*dff* t:$*DFF* %u %x:+[Q] t:$*dff* t:$*DFF* %u %d o:* %d"
    )
    for mode in ("rtl", "rtl_noinit"):
        script = lines(es.gold("chip", mode, expose=True))
        assert script.index("expose -dff") < script.index("async2sync")
        assert "expose -dff" not in es.gold("chip", mode)  # only the state check exposes


def test_every_rtl_side_models_the_simulator_not_full_case_or_parallel_case() -> None:
    """Synthesis takes `full_case` / `parallel_case` as "other selector values never happen"; a
    simulator does not. The RTL side drops them (before `proc` turns them into logic), so the
    proof finds the difference where synthesis used them."""
    for mode in ("proof", "rtl", "rtl_noinit"):
        rows = lines(es.gold("chip", mode))
        strip = "attrmap -remove full_case -remove parallel_case"
        assert strip in rows
        assert rows.index("hierarchy -check -top tt_um_chip") < rows.index(strip)
        assert rows.index(strip) < rows.index("proc")


def test_the_clock_guard_pins() -> None:
    guard = es.GUARD_CLOCK
    assert "select -set okff t:$_DFF_P_ t:$dff r:CLK_POLARITY!=0 %i %u" in guard
    assert "select -assert-none @st @okff %d" in guard
    assert "select -assert-none @okff %x:+[CLK,C] @okff %d w:clk %d" in guard
    for kind in ("t:$*latch*", "t:$mem*", "t:$anyinit", "t:$_SR_*"):
        assert kind in guard


# -- the proof scripts ---------------------------------------------------------------------------


def test_stage1_order() -> None:
    s = lines(es.stage1("chip", "netlist.v", 5))
    wanted = [
        "equiv_make gold gate equiv",
        "hierarchy -top equiv",
        "write_rtlil equiv.il",
        "write_json equiv.json",
        "equiv_simple -seq 5",
        "equiv_induct -seq 5",
        "equiv_status",
    ]
    assert contiguous(s, wanted)
    assert s.index("rename tt_um_chip gold") < s.index("rename tt_um_chip gate")
    assert s.count("design -stash gold") == 1 and s.count("design -stash gate") == 1


def test_base_script_has_the_x_run_and_the_def_run_with_ties_on_tied_points_only() -> None:
    points = [
        es.Point("uo_out[0]", "\\a", "\\b", True),
        es.Point("uo_out[1]", "\\c", "\\d", False),
    ]
    script = es.base_script(points, 7)
    x_run, def_run = script.split("log BASE-DEF")
    assert "read_rtlil base.il" in x_run and "log BASE-X" in x_run
    assert "sat -seq 7 -set-init-undef -enable_undef -set-def-inputs -set-at 1 rst_n 0" in x_run
    assert "-prove-x eqa_0 eqb_0 -prove-x eqa_1 eqb_1" in x_run
    assert "-prove-skip" not in x_run and "-set-at 1 eqa" not in x_run
    assert "-set-init-def -prove-skip 1 -set-at 1 eqa_0 eqb_0 -enable_undef" in def_run
    assert "eqa_1 eqb_1 -enable" not in def_run  # only the tied point
    assert "-show eqa_0,eqb_0,eqa_1,eqb_1,rst_n,ui_in,uio_in" in def_run


def test_miter_scripts_per_kind_of_run() -> None:
    gold, gate = "G\n", "T\n"
    base = es.miter(gold, gate, "bmc", "undef", "base", 32)
    assert base.startswith("G\nT\n")
    assert "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate miter" in base
    assert (
        "sat -tempinduct -tempinduct-baseonly -maxsteps 32 -enable_undef -set-def-inputs "
        "-set-init-undef -set-at 1 in_rst_n 0 -seq 1 -prove trigger 0 -show-ports "
        "-dump_vcd cex_bmc.vcd miter"
    ) in base
    xstep = es.miter(gold, gate, "kind_x", "undef", "xstep", 32)
    assert "sat -tempinduct -maxsteps 32" in xstep and "baseonly" not in xstep
    defstep = es.miter(gold, gate, "kind_zero", "zero", "defstep", 32)
    assert "sat -tempinduct -tempinduct-def -maxsteps 32" in defstep
    assert "-set-init-zero" in defstep


def test_scripts_never_use_the_banned_api_words() -> None:
    text = es.stage1("chip", "netlist.v", 5) + es.gold("chip", "rtl") + es.gate("chip", "n.v")
    for banned in ("FastMCP", "BinaryValue", "cocotb.runner", "units="):
        assert banned not in text
