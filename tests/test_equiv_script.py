# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    assert contiguous(gold, [*es.PRODUCTION_FSM_PASSES, "opt_clean"])
    # only a listing of the RTL's memories (it changes nothing) sits between them and `memory`
    between = gold[gold.index("fsm") + 3 : gold.index("memory")]
    assert between == [f"log {es.MEMS_BEGIN}", "select -list t:$mem*", f"log {es.MEMS_END}"]
    assert gold.index("memory") < gold.index("techmap t:$pmux") < gold.index("attrmap -remove init")


def test_what_the_production_script_removes_is_removed_from_the_rtl() -> None:
    back = lines(synth_script.back_script("tt_um_chip", "synth", 100.0, ["c"]))
    deleted = {ln.split()[1] for ln in [*front(), *back] if ln.startswith("delete ")}
    assert deleted == {"t:$print", "t:$assert"}
    assert deleted <= set(es.DELETE_NON_HARDWARE.split())
    assert "tribuf" in front()


def test_the_rtl_is_read_with_the_production_parser_flags_but_no_defines() -> None:
    read = lines(es.gold("chip", "proof"))[0]
    prod = next(ln for ln in front() if ln.startswith("read_verilog"))
    for flag in ("-defer", "-noautowire", "-sv"):
        assert flag in read and flag in prod
    # the proof reads cleaned files with no macro left in them; no implicit SYNTHESIS either
    assert "-nosynthesis" in read and "-nosynthesis" not in prod
    assert " -D" not in read and all(f"-D{d}" in prod for d in synth_script.DEFINES)


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


def test_the_rtl_side_reads_the_cleaned_files_with_no_attribute_surgery() -> None:
    """The host blanks comments and attributes out of the RTL files (`equiv_sv.clean`) before
    Yosys reads them, so the scripts have nothing to strip (a Yosys-level `attrmap` after
    `read_verilog` does nothing: the attributes are already applied)."""
    for mode in ("proof", "rtl", "rtl_noinit"):
        assert "full_case" not in es.gold("chip", mode)
        assert "attrmap -remove full_case" not in es.gold("chip", mode)


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
    script = es.base_script(points, 7, {"gold": "ff_gold", "gate": "ff_gate"})
    x_run, def_run = script.split("log BASE-DEF")
    assert "read_rtlil base.il" in x_run and "log BASE-X" in x_run
    # Round 15 n40: the X run reads each side on its own (the RTL side from a copy that keeps
    # the RTL's value at every `$equiv`, the netlist side from one that keeps the netlist's),
    # so a netlist gate never reads an RTL register's x; the DEF run reads `equiv` as before.
    assert x_run.startswith(
        "read_rtlil base.il\ndesign -save base\n"
        "copy equiv equiv_rtl\nequiv_remove -gold equiv_rtl\n"
        "copy equiv equiv_net\nequiv_remove -gate equiv_net\n"
        "hierarchy -top base_x\nflatten\nopt_clean\nlog BASE-X\nsat -seq 7 "
    )
    assert x_run.endswith("-dump_vcd base_x.vcd base_x\ndesign -load base\nhierarchy -top equiv\n")
    assert def_run.rstrip().endswith("-dump_vcd base_def.vcd equiv")
    assert (es.BASE_X_TOP, es.BASE_X_RTL, es.BASE_X_NETLIST) == ("base_x", "equiv_rtl", "equiv_net")
    # RTL registers x, netlist flip-flops any defined value, and nothing else fixed.
    assert (
        "sat -seq 7 -set-all-undef-at 1 ff_gold -set-def-at 1 ff_gate -enable_undef "
        "-set-def-inputs -set-at 1 rst_n 0"
    ) in x_run
    assert "-set-init" not in x_run
    assert "-prove-x eqa_0 eqb_0 -prove-x eqa_1 eqb_1" in x_run
    assert "-prove-skip" not in x_run and "-set-at 1 eqa" not in x_run
    assert "-set-init-def -prove-skip 1 -set-at 1 eqa_0 eqb_0 -enable_undef" in def_run
    assert "eqa_1 eqb_1 -enable" not in def_run  # only the tied point
    assert "-show eqa_0,eqb_0,eqa_1,eqb_1,rst_n,ui_in,uio_in" in def_run


def test_the_power_up_options_follow_the_sides_with_flip_flops() -> None:
    assert es.power_up({"gold": "ff_gold", "gate": "ff_gate_1"}) == (
        "-set-all-undef-at 1 ff_gold -set-def-at 1 ff_gate_1"
    )
    assert es.power_up({"gate": "ff_gate"}) == "-set-def-at 1 ff_gate"
    assert es.power_up({}) == ""
    assert "-set-init" not in es.base_script([], 3, {}).split("log BASE-DEF")[0]


def test_the_referee_writes_the_miter_then_searches_with_the_per_side_power_up() -> None:
    model = es.miter_model("G\n", "T\n")
    assert model.startswith("G\nT\n") and "sat" not in model
    assert model.endswith(f"{es.MITER}\nhierarchy -top miter\nwrite_rtlil miter.il\n")
    bmc = es.miter_bmc("bmc", {"gold": "ff_gold", "gate": "ff_gate"}, 32)
    assert bmc.startswith("read_rtlil miter_ff.il\nhierarchy -top miter\n")
    assert (
        "sat -tempinduct -tempinduct-baseonly -maxsteps 32 -enable_undef -set-def-inputs "
        "-set-all-undef-at 1 ff_gold -set-def-at 1 ff_gate -set-at 1 in_rst_n 0 -seq 1 "
        "-prove trigger 0 -show-ports -dump_vcd cex_bmc.vcd miter"
    ) in bmc
    assert "-set-init" not in bmc


def test_the_one_shot_referee_is_the_same_search_as_one_sat_problem() -> None:
    """`miter_once` has the constraints of `miter_bmc` (x semantics, defined inputs, the per-side
    power-up, rst_n low at cycle 1), as one problem of time steps 1 to depth + 1."""
    once = es.miter_once("bmc_once", {"gold": "ff_gold", "gate": "ff_gate"}, 32)
    assert once == (
        "read_rtlil miter_ff.il\nhierarchy -top miter\n"
        "sat -seq 33 -prove-skip 1 -enable_undef -set-def-inputs -set-all-undef-at 1 ff_gold "
        "-set-def-at 1 ff_gate -set-at 1 in_rst_n 0 -prove trigger 0 -show-ports "
        "-dump_vcd cex_bmc_once.vcd miter\n"
    )


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


@pytest.mark.parametrize("mode", ["proof", "rtl", "rtl_noinit"])
def test_the_rtl_side_keeps_a_case_of_constants_as_logic(mode: str) -> None:
    """Round 14 v66/a13: as a ROM (`proc_rom`), a registered seven-segment table with a constant
    output bit lost that bit's data input once `memory` merged the output register into the ROM,
    and `check -assert` refused the model as an undriven net. `proc -norom` keeps it as logic
    (the same function); every RTL model of the check (stages 1 to 4) is built this way."""
    gold = lines(es.gold("chip", mode))
    assert gold[len(es._read_rtl("chip"))] == "proc -norom"
    assert "proc" not in gold and "proc_rom" not in " ".join(gold)


def test_the_elaboration_reads_the_rtl_like_the_proof_and_writes_it_unprocessed() -> None:
    """C5: exactly the `_read_rtl` lines and `write_rtlil`: no `proc` (it would turn the processes
    whose positions are compared into cells) and nothing else that could change them."""
    assert (
        es.elaboration("chip") == "\n".join([*es._read_rtl("chip"), "write_rtlil elab.il"]) + "\n"
    )
    assert "proc" not in es.elaboration("chip")
