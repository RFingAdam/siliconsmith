# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Yosys scripts of the equivalence check (RTL against the sky130 netlist).

Pure text: every function returns the script for one Yosys call, and the log markers the
parser (`equiv_parse.py`) looks for are constants here, so the two cannot drift apart. The
orchestration is in `equiv.py`. Stdlib only: this runs in the container.

## What the check is (stages)

0. The netlist text must not contain an `x`/`z` constant (our synthesis ties undefined inputs
   low, so a correct netlist has none).
1. Guards plus the proof. The RTL side ("gold") is the design as the PRODUCTION synthesis sees it
   just before technology mapping (same `proc`, `flatten`, `opt -nodffe -nosdff`, `fsm`, `opt`,
   memories to flops, `$pmux` to gates, `$print`/`$assert` removed, initial values removed). The
   netlist side ("gate") is read from the netlist FILE the synthesis wrote. Both sides refuse
   what the proof cannot model (see the `GUARD_*` blocks). `equiv_make` pairs signals by name,
   `equiv_simple -seq 5` and `equiv_induct -seq 5` prove the induction step.
2. If the RTL has initial values: the state check. The RTL with them against the RTL without
   them, compared on every register after the one reset cycle (cycle 2). Equal state at cycle 2
   means the initial values change nothing afterwards, with no cycle limit. If some register
   differs (it keeps an initial value that reset does not set), a bounded search from reset
   (`depth` cycles) looks for the output it changes: found is `not_equivalent`, not found is
   `not_proven` (never a pass).
3. The base case of the stage-1 induction, on the stage-1 model itself (`equiv.il`), for cycles
   1..seq+2, in two runs: every RTL register powers up `x` and every netlist flip-flop powers up
   with any defined value, as on a chip (X); and every flip-flop powers up defined, a netlist
   flip-flop equal to the RTL register it is paired with (DEF). Stages 1 and 3 together are a
   complete induction proof.
4. A referee from reset that does not depend on name pairing or on the `fsm` pass: the RTL as
   written (initial values removed: stage 2 showed they change nothing) against the netlist, a
   bounded search (`depth` cycles) with the power-up of stage 3 (RTL registers `x`, netlist
   flip-flops any defined value); or k-induction twice (`kind_x`, `kind_zero`, every flip-flop
   `x`, then every flip-flop 0) when stage 1 did not prove everything.

Cycle 1 is power-up with `rst_n` low; outputs are compared from cycle 2 on.

## Keeping in step with the synthesis script

`PRODUCTION_FSM_PASSES` and `PRODUCTION_MEMORY_PASSES` are the pass lines of
`synth_script.front_script` that the RTL side repeats. `tests/test_equiv_script.py` fails when the
production script changes them and this file does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from siliconsmith.worker import synth_script

LIBERTY = synth_script.LIBERTY
OUTPUTS = ("uo_out", "uio_out", "uio_oe")

# The pass lines of synth_script.front_script that the RTL side repeats, in order.
PRODUCTION_FSM_PASSES = ("opt -nodffe -nosdff", "fsm", "opt")
PRODUCTION_MEMORY_PASSES = ("memory -nomap", "memory_map")
# What the production synthesis removes before mapping (`delete t:$print`, `delete t:$assert`;
# the check cells go with `hierarchy -nokeep_asserts`).
DELETE_NON_HARDWARE = "delete t:$print t:$check t:$assert t:$assume t:$cover t:$live t:$fair"

# Log markers. Each guard prints its marker first, so the last marker in a failed log names the
# guard that refused.
M_CLOCKING = "GUARD-CLOCKING"
M_TRISTATE = "GUARD-TRISTATE"
M_ASYNC_SR = "GUARD-ASYNCSR"
M_STRUCTURE = "GUARD-STRUCTURE"
M_CELLS = "GUARD-CELLS"
M_MODULES = "GUARD-MODULES"
M_EXPOSE = "GUARD-EXPOSE"
M_OK = "GUARD-OK"
GATE_RTLIL = "gate_read.il"  # the netlist as Yosys read it (the host looks for x/z in it)
INITS_BEGIN = "INITS-BEGIN"
INITS_END = "INITS-END"
MEMS_BEGIN = "MEMS-BEGIN"  # the RTL's memories, listed before the `memory` passes
MEMS_END = "MEMS-END"
BASE_PREFIX = "BASE-"

GUARD_CLOCK = (
    f"log {M_CLOCKING}\n"
    "select -set st t:$*dff* t:$*DFF* t:$ff t:$_FF_ t:$*latch* t:$*LATCH* t:$sr t:$_SR_* "
    "t:$mem* t:$anyconst t:$anyinit t:$allconst t:$allseq\n"
    "select -set okff t:$_DFF_P_ t:$dff r:CLK_POLARITY!=0 %i %u\n"
    "select -assert-none @st @okff %d\n"
    "select -assert-none @okff %x:+[CLK,C] @okff %d w:clk %d\n"
    f"log {M_OK}\n"
)
GUARD_TRISTATE = f"log {M_TRISTATE}\nselect -assert-none t:$tribuf t:$_TBUF_\n"
GUARD_ASYNC_SR = (
    f"log {M_ASYNC_SR}\n"
    "select -assert-none t:$dffsr t:$dffsre t:$_DFFSR_* t:$_DFFSRE_* t:$aldff* t:$_ALDFF*\n"
)
GUARD_STRUCTURE = f"log {M_STRUCTURE}\ncheck -assert\n"
# After `flatten` the netlist may hold only plain gates (`$_AND_`, `$_DFF_P_`, ...): a leftover
# `$shiftx`, `$div` or a cell without a function is behaviour that the proof models differently
# from a simulator (out-of-range bit selects, division by zero). `$scopeinfo` is bookkeeping.
GUARD_CELLS = f"log {M_CELLS}\nselect -assert-none t:* t:$_* %d t:$scopeinfo %d\n"


def guard_modules(name: str) -> str:
    """The netlist file may define the top module and nothing else.

    Run right after the file is read and BEFORE the liberty file is: then every module that
    exists is one the file defined, and the top module (with everything in it) is removed from
    the selection. A module the file wrote for a sky130 cell (one the liberty read skipped, a
    clock gate) would replace the real cell with whatever the file says. This does not look at
    `src` attributes, which the file can spell any way it likes.
    """
    return f"log {M_MODULES}\nselect -assert-none * tt_um_{name} %d\n"


# Every flip-flop output must be a port after `expose -dff`; else some register is invisible to
# the state check (the check then cannot vouch for it).
GUARD_EXPOSE = (
    f"log {M_EXPOSE}\n"
    "expose -dff\n"
    "select -assert-none t:$*dff* t:$*DFF* %u %x:+[Q] t:$*dff* t:$*DFF* %u %d o:* %d\n"
)


def _read_rtl(name: str) -> list[str]:
    """Read the RTL the proof compares with: the cleaned copies (`equiv_sv.clean`: no comment, no
    attribute), with the same parser flags as the production synthesis but no defines, and
    `-nosynthesis` (no implicit SYNTHESIS macro; the text has no macro left to test it)."""
    return [
        f"read_verilog -defer -noautowire -sv -nosynthesis project.v {name}.v",
        f"hierarchy -check -top tt_um_{name}",
    ]


def elaboration(name: str) -> str:
    """The RTL as the proof reads it, elaborated and written out as RTLIL (`elab.il`): the host
    reads the source position of every process in it (`equiv_elab`)."""
    return "\n".join([*_read_rtl(name), "write_rtlil elab.il"]) + "\n"


def gold(name: str, mode: str, expose: bool = False) -> str:
    """The RTL side.

    `mode`: `proof` (stages 1 and 3: as synthesis sees it before mapping, initial values
    removed), `rtl` (as written, initial values kept) or `rtl_noinit` (as written, initial values
    removed: what a chip does at power-up). `expose`: every flip-flop output becomes a port, for
    the state check; it must happen BEFORE `async2sync`, which moves the output of a flip-flop
    with an asynchronous reset to an internal wire that `expose` skips.
    """
    if mode not in ("proof", "rtl", "rtl_noinit"):
        raise ValueError(f"unknown mode {mode!r}")
    s = _read_rtl(name)
    s += ["proc", "flatten", DELETE_NON_HARDWARE, "opt_expr", "opt_clean"]
    if mode == "proof":
        s += [*PRODUCTION_FSM_PASSES, "opt_clean"]
        s += [
            f"log {MEMS_BEGIN}",
            "select -list t:$mem*",
            f"log {MEMS_END}",
            "memory",
            "opt_clean",
            # $pmux as the gates synthesis uses (OR of the selected cases). It only differs when
            # several selects are high, which `case` (proc) and fsm one-hot never reach.
            "techmap t:$pmux",
            "opt_clean",
            f"log {INITS_BEGIN}",
            "select -list a:init",
            f"log {INITS_END}",
            "attrmap -remove init",
        ]
    else:
        s += ["memory", "opt_clean"]
        if mode == "rtl_noinit":
            s += ["attrmap -remove init"]
    s += ["splitnets", "tribuf", GUARD_TRISTATE, GUARD_STRUCTURE.rstrip("\n")]
    if expose:
        s += [GUARD_EXPOSE.rstrip("\n")]
    s += ["async2sync", "dffunmap", GUARD_CLOCK.rstrip("\n")]
    return "\n".join(s) + "\n"


def gate(name: str, netlist: str) -> str:
    """The netlist side, read from the netlist FILE."""
    return (
        "\n".join(
            [
                f"read_verilog {netlist}",
                guard_modules(name).rstrip("\n"),
                f"read_liberty -ignore_miss_func {LIBERTY}",  # cell functions (dlclkp has none)
                f"hierarchy -check -top tt_um_{name}",
                # The netlist as Yosys understood it, before anything is changed. The host reads
                # it for x/z constants whatever way they were spelled (`setundef` only replaces x,
                # and Yosys's SAT reads a z as a plain 0).
                f"write_rtlil {GATE_RTLIL}",
                "flatten",
                # An `init` attribute in the netlist would give a flip-flop a power-up value that
                # a chip does not have: the netlist side never has initial values.
                "attrmap -remove init",
                "opt_clean",
                # An undriven net (a cell input left open) is refused BEFORE `setundef`: made a
                # free value per cycle, `n | ~n` would be 1 in the proof while the netlist is x.
                GUARD_STRUCTURE.rstrip("\n"),
                # An x or z constant the text guard missed becomes a free value. The host refuses
                # such a netlist anyway (`write_rtlil` above); this is a second line of defence.
                "setundef -undriven -anyseq",
                GUARD_CELLS.rstrip("\n"),
                GUARD_TRISTATE.rstrip("\n"),
                GUARD_ASYNC_SR.rstrip("\n"),
                "async2sync",
                "dffunmap",
                GUARD_CLOCK.rstrip("\n"),
            ]
        )
        + "\n"
    )


def stash(script: str, name: str, as_: str) -> str:
    return script + f"rename tt_um_{name} {as_}\ndesign -stash {as_}\n"


def state_check(name: str) -> str:
    """Stage 2: is the RTL's state after the reset cycle the same without its initial values?

    The RTL as written (initial values kept) against the RTL without them, with the flip-flops of
    both exposed as outputs and every flip-flop of the second powering up `x`, compared at cycle 2
    (the state after the one reset cycle). If every register the RTL defines is equal there, the
    initial values change nothing from cycle 2 on: both copies are the same logic in the same
    state. No cycle limit. A difference means some register keeps an initial value that reset
    does not set.
    """
    rtl = stash(gold(name, "rtl", expose=True), name, "gold")
    noinit = stash(gold(name, "rtl_noinit", expose=True), name, "gate")
    return (
        rtl
        + noinit
        + both()
        + "\n".join(
            [
                "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate miter",
                "hierarchy -top miter",
                "sat -seq 2 -prove-skip 1 -enable_undef -set-def-inputs -set-init-undef "
                "-set-at 1 in_rst_n 0 -prove trigger 0 -show-ports -dump_vcd cex_state.vcd miter",
            ]
        )
        + "\n"
    )


def both(a: str = "gold", b: str = "gate") -> str:
    return f"design -copy-from {a} -as gold {a}\ndesign -copy-from {b} -as gate {b}\n"


def stage1(name: str, netlist: str, seq: int) -> str:
    """Guards and the induction step: writes `equiv.il` and `equiv.json` before the proof."""
    return (
        stash(gold(name, "proof"), name, "gold")
        + stash(gate(name, netlist), name, "gate")
        + both()
        + "\n".join(
            [
                "equiv_make gold gate equiv",
                "hierarchy -top equiv",
                "write_rtlil equiv.il",
                "write_json equiv.json",
                f"equiv_simple -seq {seq}",
                f"equiv_induct -seq {seq}",
                "equiv_status",
            ]
        )
        + "\n"
    )


@dataclass(frozen=True)
class Point:
    """One `$equiv` cell of the stage-1 model: what the RTL and the netlist drive there."""

    label: str
    a: str  # RTLIL text of the RTL (gold) bit
    b: str  # RTLIL text of the netlist (gate) bit
    tie: bool  # the netlist flop may power up equal to the RTL register (DEF run)


def power_up(wires: Mapping[str, str]) -> str:
    """The power-up of the X run and the referee, as `sat` options: every RTL register `x`
    (`-set-all-undef-at 1`), every netlist flip-flop ANY defined value (`-set-def-at 1`), as on
    a chip. `wires`: `{side: wire}`, the wire holding every flip-flop output of that side
    (`equiv_parse._power_up_wires`; a side with no flip-flop has none). No `-set-init-*`
    option, so nothing else is fixed. A netlist value is then never an x that only comes from
    spreading x gate by gate: it is the exact value for some power-up state, and every value
    the RTL defines must equal it for every one of them."""
    opt = {"gold": "-set-all-undef-at", "gate": "-set-def-at"}
    return " ".join(f"{opt[side]} 1 {wires[side]}" for side in ("gold", "gate") if side in wires)


def base_script(points: list[Point], steps: int, wires: Mapping[str, str]) -> str:
    """Base case of the stage-1 induction (stage 3): the X run and the DEF run.

    The model `base.il` is `equiv.il` plus one named wire per side of every point and the
    power-up wires `wires` (`equiv_parse.base_model`). Users of a matched signal read the RTL
    value, as in `equiv_induct`. X run: `power_up`. DEF run: every flip-flop powers up defined,
    a netlist flip-flop equal to the RTL register it is paired with.
    """
    prove = " ".join(f"-prove-x eqa_{i} eqb_{i}" for i in range(len(points)))
    show = ",".join(f"eqa_{i},eqb_{i}" for i in range(len(points)))
    ties = " ".join(f"-set-at 1 eqa_{i} eqb_{i}" for i, p in enumerate(points) if p.tie)
    script = "read_rtlil base.il\nhierarchy -top equiv\n"
    for tag, extra in (("X", power_up(wires)), ("DEF", f"-set-init-def -prove-skip 1 {ties}")):
        script += (
            f"log {BASE_PREFIX}{tag}\n"
            f"sat -seq {steps} {extra} -enable_undef -set-def-inputs -set-at 1 rst_n 0 {prove} "
            f"-show {show},rst_n,ui_in,uio_in -dump_vcd base_{tag.lower()}.vcd equiv\n"
        )
    return script


# induct: base (bounded search only), xstep (k-induction, x states allowed in the step: matches
# the x power-up base) or defstep (k-induction over defined states only).
SAT_MODE = {
    "base": "-tempinduct -tempinduct-baseonly",
    "xstep": "-tempinduct",
    "defstep": "-tempinduct -tempinduct-def",
}


MITER = "miter -equiv -flatten -make_outputs -ignore_gold_x gold gate miter"


def miter_model(golds: str, gates: str) -> str:
    """The referee of stage 4, step 1: build the miter of two stashed designs and write it
    (`miter.il`), so that `equiv_parse.miter_model` can add the power-up wires of each side."""
    return golds + gates + both() + f"{MITER}\nhierarchy -top miter\nwrite_rtlil miter.il\n"


def miter_bmc(tag: str, wires: Mapping[str, str], depth: int) -> str:
    """The referee of stage 4, step 2: a bounded search from reset (`depth` cycles) on
    `miter_ff.il` with the per-side `power_up`. As `miter`, outputs from cycle 2 on."""
    return (
        "read_rtlil miter_ff.il\nhierarchy -top miter\n"
        f"sat {SAT_MODE['base']} -maxsteps {depth} -enable_undef -set-def-inputs "
        f"{power_up(wires)} -set-at 1 in_rst_n 0 -seq 1 -prove trigger 0 -show-ports "
        f"-dump_vcd cex_{tag}.vcd miter\n"
    )


def miter(golds: str, gates: str, tag: str, init: str, induct: str, depth: int) -> str:
    """Stages 2 and 4: a miter of two stashed designs and a `sat` run from reset.

    `-enable_undef` is REQUIRED with `-ignore_gold_x` (without it `$eqx` masks hide real
    mismatches). `-seq 1`: outputs are compared from cycle 2 (cycle 1 is power-up, `rst_n` low).
    """
    return (
        golds
        + gates
        + both()
        + "\n".join(
            [
                MITER,
                "hierarchy -top miter",
                f"sat {SAT_MODE[induct]} -maxsteps {depth} -enable_undef -set-def-inputs "
                f"-set-init-{init} -set-at 1 in_rst_n 0 -seq 1 -prove trigger 0 -show-ports "
                f"-dump_vcd cex_{tag}.vcd miter",
            ]
        )
        + "\n"
    )
