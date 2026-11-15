# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""check_equivalence against the real toolchain image, through the real MCP tools (M1-3).

`test_equiv_mismatch` gives the worker's check function a hand-edited netlist through the
TEST-ONLY script `tests/fixtures/equiv/check_netlist.py`, run in the container the way the runner
runs the worker. The production `equiv` verb has no way to substitute a netlist.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT
from mcp import Client
from netlist_edit import (
    async_set_and_reset,
    behavioural_bit_select,
    clock_from_inverter,
    combinational_loop,
    hold_flop_on_tied_output,
    hold_flop_with_init_attribute,
    include_directive,
    open_cell_input,
    own_cell_module,
    own_cell_module_hidden_by_macro,
    own_cell_module_with_spoofed_src,
    swap_cell_type,
    z_constant,
    z_constant_spelled,
)

from integration.helpers import run_hardened
from integration.test_emit import FRONTENDS, call
from integration.test_ledger import step_states
from integration.test_sim import counter_project
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server
from siliconsmith.worker import equiv_sv

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

EQUIV_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "equiv"
DESIGNS = REPO_ROOT / "tests" / "fixtures" / "equiv_designs"
SOURCE_FILE = {"amaranth": "amaranth.py", "myhdl": "myhdl.py", "verilog": "verilog.v"}
SOURCE_PATH = {"amaranth": "hdl/{}.py", "myhdl": "hdl/{}.py", "verilog": "src/{}.v"}


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


def check_in_container(
    image: str, project: Path, netlist: str, scratch: str = "/work/build/eq_scratch"
) -> dict[str, Any]:
    """The worker's `check` on the project's emitted RTL (`src/`) and a netlist file in build/."""
    proc = run_hardened(
        image,
        project,
        "python",
        "/smoke/check_netlist.py",
        "/work/src",
        project.name,
        netlist,
        scratch,
        mount=EQUIV_FIXTURES,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stderr
    verdict: dict[str, Any] = json.loads(proc.stdout.strip().splitlines()[-1])
    return verdict


async def design_project(client: Client, root: Path, name: str, fe: str) -> Path:
    """A project `name` whose design is tests/fixtures/equiv_designs/<name>/<front-end file>."""
    created = await call(
        client,
        "create_project",
        name=name,
        frontend=fe,
        author="Ada Lovelace",
        description=f"The {name} equivalence fixture.",
    )
    assert created["ok"] is True, created["summary"]
    source = (DESIGNS / name / SOURCE_FILE[fe]).read_bytes().decode("utf-8")  # a CR stays a CR
    wrote = await call(
        client, "write_rtl", project=name, path=SOURCE_PATH[fe].format(name), content=source
    )
    assert wrote["ok"] is True, wrote["summary"]
    return root / name


@pytest.mark.parametrize("fe", FRONTENDS, ids=[f"counter-{fe}" for fe in FRONTENDS])
async def test_equiv(server: Client, tmp_path: Path, fe: str) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, fe)
        started = time.monotonic()
        result = await call(client, "check_equivalence", project="counter")
        took = time.monotonic() - started
        assert result["ok"] is True, result["summary"]
        first = result["summary"].splitlines()[0]
        assert first.startswith(
            "Equivalent: the sky130 netlist does the same as the RTL (proven by"
        )
        details = result["details"]
        assert details["stage"] == "equivalent" and details["verdict"] == "equivalent"
        assert details["method"] == "equiv_induct" and details["reason"] is None
        assert details["stage1"]["unproven"] == 0 and details["base"]["status"] == "ok"
        assert details["frontend"] == fe and details["top"] == "tt_um_counter"
        assert len(details["netlist_sha256"]) == 64
        assert details["synth"]["flip_flops"] == 8
        print(f"\ncounter-{fe}: whole call {took:.1f} s, check seconds {details['seconds']}")

        # The netlist stays in the run folder; build/synth belongs to synthesize.
        assert not (project / "build" / "synth").exists()
        assert not list((project / "build" / ".ss").glob("*"))
        states = step_states(await call(client, "status", project="counter"))
        assert states["equivalence"] == "pass"
        assert states["synthesis"] == "not_run"  # an equivalence run never sets another row


@pytest.mark.parametrize(("name", "hidden"), [("lofold", True), ("lofoldn", False)])
async def test_the_lint_without_comments_finds_a_race_a_comment_hid(
    server: Client, tmp_path: Path, image: str, name: str, hidden: bool
) -> None:
    """Round 21: the real Verilator, run on the RTL with its comments removed, reports the
    BLKSEQ that `/* verilator lint_off WIDTH,//,BLKSEQ */` hid from the emit lint (called past
    the text rule, which refuses the same comment first); the control with nonblocking
    assignments gives nothing."""
    async with server as client:
        project = await design_project(client, tmp_path, name, "verilog")
    proc = run_hardened(
        image,
        project,
        "python",
        "/smoke/hidden_lint.py",
        "/work/src",
        name,
        "/work/build/hidden",
        mount=EQUIV_FIXTURES,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    found = json.loads(proc.stdout.strip().splitlines()[-1])
    if hidden:
        assert found[0] == "guard_sim_synth_differ", found
        assert f"({name}.v, line 16)" in found[1] and "BLKSEQ" in found[1], found
    else:
        assert found is None, found


async def test_equiv_mismatch(server: Client, tmp_path: Path, image: str) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        synthesized = await call(client, "synthesize", project="counter")
        assert synthesized["ok"] is True, synthesized["summary"]
        original = (project / "build" / "synth" / "tt_um_counter.v").read_text()

        # The untouched production netlist is equivalent (the control).
        (project / "build" / "unedited.v").write_text(original)
        control = check_in_container(image, project, "/work/build/unedited.v")
        assert control["verdict"] == "equivalent" and control["ok"] is True, control["message"]

        # One cell changed to a different function: not equivalent, with the pin and the cycle.
        edited, what = swap_cell_type(original)
        (project / "build" / "edited.v").write_text(edited)
        started = time.monotonic()
        verdict = check_in_container(image, project, "/work/build/edited.v")
        print(f"\nmismatch ({what}): {time.monotonic() - started:.1f} s, {verdict['seconds']}")
        assert verdict["verdict"] == "not_equivalent" and verdict["ok"] is False, verdict
        mismatch = verdict["first_mismatch"]
        assert mismatch["cycle"] >= 2
        assert any(d["signal"].startswith("uo_out[") for d in mismatch["differs"]), mismatch
        diff = mismatch["differs"][0]
        assert {diff["rtl"], diff["netlist"]} <= {"0", "1", "x"} and diff["rtl"] != diff["netlist"]
        assert verdict["trace"] and verdict["trace"][-1]["cycle"] == mismatch["cycle"]
        assert "NOT a pass" not in verdict["message"]  # a difference, not a "could not decide"


# -- more designs, all three front-ends: hidden state (shift register) and a state machine -------

# Flip-flops after synthesis: 16 for the shift register; 3 state bits (binary) or 5 (one-hot, which
# Yosys's fsm pass may choose) for the detector, so only a range is asserted there.
FLOPS = {"shiftpar": (16, 16), "seqdet": (3, 5)}


@pytest.mark.parametrize("design", sorted(FLOPS))
@pytest.mark.parametrize("fe", FRONTENDS)
async def test_equiv_designs(server: Client, tmp_path: Path, design: str, fe: str) -> None:
    async with server as client:
        await design_project(client, tmp_path, design, fe)
        started = time.monotonic()
        result = await call(client, "check_equivalence", project=design)
        took = time.monotonic() - started
        assert result["ok"] is True, result["summary"] + json.dumps(result["details"], indent=1)
        details = result["details"]
        assert details["stage"] == "equivalent" and details["verdict"] == "equivalent"
        assert details["method"] == "equiv_induct" and details["reason"] is None
        assert details["stage1"]["unproven"] == 0 and details["base"]["status"] == "ok"
        low, high = FLOPS[design]
        assert low <= details["synth"]["flip_flops"] <= high
        print(f"\n{design}-{fe}: whole call {took:.1f} s, check seconds {details['seconds']}")
        states = step_states(await call(client, "status", project=design))
        assert states["equivalence"] == "pass"


# -- regression seeds from the attack rounds ----------------------------------------------------
# Netlist seeds: one real production counter netlist, ten hand edits, ONE container call (the
# synthesis is the slow part). Each answer must be one of the refusals named here, never a pass.

NETLIST_SEEDS = {
    "hold": (hold_flop_on_tied_output, "not_equivalent", None),
    "async": (async_set_and_reset, "not_proven", "guard_async_set_reset"),
    "z": (z_constant, "error", "xz_constant"),
    # the same z in the spellings a plain text search misses (found by the validator)
    "z_unsized": (z_constant_spelled("'bz"), "error", "xz_constant"),
    "z_blanks": (z_constant_spelled("1 'b z"), "error", "xz_constant"),
    "z_hex": (z_constant_spelled("'hz"), "error", "xz_constant"),
    "z_fill": (z_constant_spelled("'z"), "error", "xz_constant"),
    # a netlist `init` attribute must not give a flop a power-up value
    "init_attr": (hold_flop_with_init_attribute, "not_equivalent", None),
    "clkinv": (clock_from_inverter, "not_proven", "guard_clocking"),
    "loop": (combinational_loop, "error", "structure"),
    # a floating cell input is x in simulation: never a free value in the proof
    "open_pin": (open_cell_input, "error", "structure"),
    # the file must not supply its own module for a library cell
    "own_cell": (own_cell_module, "error", "netlist_modules"),
    "own_cell_src": (own_cell_module_with_spoofed_src, "error", "netlist_modules"),
    "include": (include_directive, "error", "netlist_modules"),
    # the `module` keyword behind a macro: no text search sees it, the Yosys guard must
    "own_cell_macro": (own_cell_module_hidden_by_macro, "error", "netlist_modules"),
    # behavioural code (a bit select) models an out-of-range index differently from a simulator
    "bit_select": (behavioural_bit_select, "error", "cells"),
}


async def test_equiv_netlist_seeds(server: Client, tmp_path: Path, image: str) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        synthesized = await call(client, "synthesize", project="counter")
        assert synthesized["ok"] is True, synthesized["summary"]
        original = (project / "build" / "synth" / "tt_um_counter.v").read_text()
        commands = []
        for name, (seed, _, _) in NETLIST_SEEDS.items():
            edited, what = seed(original)
            assert edited != original, what
            (project / "build" / f"seed_{name}.v").write_text(edited)
            commands.append(
                f"python /smoke/check_netlist.py /work/src counter /work/build/seed_{name}.v "
                f"/work/build/eq_{name} | tail -n 1"
            )
        started = time.monotonic()
        proc = run_hardened(
            image,
            project,
            "sh",
            "-c",
            "; ".join(commands),
            mount=EQUIV_FIXTURES,
            timeout=900,
        )
        assert proc.returncode == 0, proc.stderr
        verdicts = [json.loads(line) for line in proc.stdout.strip().splitlines()]
        assert len(verdicts) == len(NETLIST_SEEDS), proc.stdout
        print(f"\nnetlist seeds in one container call: {time.monotonic() - started:.1f} s")
        for (name, (_, verdict, reason)), got in zip(NETLIST_SEEDS.items(), verdicts, strict=True):
            print(f"seed {name}: {got['verdict']} {got['reason']}: {got['message']}")
            assert got["ok"] is False, name
            assert (got["verdict"], got["reason"]) == (verdict, reason), (name, got)
            if verdict == "not_proven":
                assert got["message"].endswith("This is NOT a pass."), name


# RTL seeds go through the production tool: the problem is in the RTL itself.
RTL_SEEDS = [
    ("initz", "verilog", "not_equivalent", "rtl_initial_value"),
    ("initz", "amaranth", "not_equivalent", "rtl_initial_value"),
    ("fallingedge", "verilog", "not_proven", "guard_clocking"),
    # an initial value that first reaches a pin at cycle 42: past the bounded search from reset,
    # so it must be not proven (it used to be called equivalent, which was wrong)
    ("deepinit", "verilog", "not_proven", "initial_values"),
    ("deepinit", "amaranth", "not_proven", "initial_values"),
    # the same, but the register has an asynchronous clear (tied off, in a sub-block or a clock
    # domain): the initial-value check used to miss these and said equivalent
    ("subarst", "verilog", "not_proven", "initial_values"),
    ("carst", "verilog", "not_proven", "initial_values"),
    ("carst", "amaranth", "not_proven", "initial_values"),
    # RTL that reads one way in simulation and another in synthesis
    ("synthdef", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("xoff", "verilog", "not_equivalent", None),  # translate_off is ignored, as in a simulator
    # Round 3: the proof reads the RTL with comments and attributes blanked out, the netlist was
    # synthesized from the original text. Hot comments and attributes that make synthesis build
    # other gates (the reviewer saw the outputs differ in Icarus from cycle 2 on) are reported.
    ("fchot", "verilog", "not_equivalent", None),
    ("fccomblo", "verilog", "not_proven", "guard_sim_synth_differ"),  # lint_off hides the warning
    ("fcfunclo", "verilog", "not_proven", "guard_sim_synth_differ"),  # lint_off hides the warning
    ("xoffglued", "verilog", "not_equivalent", None),
    ("nosync", "verilog", "not_equivalent", None),
    # what a simulator and the synthesis read differently and cannot be neutralized is refused
    ("ificarus", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("ifverilator", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("uqdodgelo", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("bitreg", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("delay", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("nolatchlo", "verilog", "not_proven", "guard_sim_synth_differ"),  # lint_off LATCH
    # Round 4: constructs a simulator reads differently from Yosys, refused with the construct named
    ("sensn", "verilog", "not_proven", "guard_sim_synth_differ"),  # always @(ui_in[0])
    ("fstaticn", "verilog", "not_proven", "guard_sim_synth_differ"),  # function without automatic
    ("racen", "verilog", "not_proven", "guard_sim_synth_differ"),  # lint_off BLKSEQ hides a race
    ("lpnosync", "verilog", "not_proven", "guard_sim_synth_differ"),  # a macro builds an attribute
    ("eqx", "verilog", "not_proven", "guard_sim_synth_differ"),  # r === 1'bx
    ("enumreg", "verilog", "not_proven", "guard_sim_synth_differ"),  # an enum is a two-state int
    # Round 5: characters that the tools read differently (a no-break space after an escaped
    # identifier hides a translate_off region or a delay from a lexer that treats it as a blank)
    ("nbspoff2", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("nbspdel", "verilog", "not_proven", "guard_sim_synth_differ"),
    # MyHDL `@always(b0)`: written as `always @(b0)`, a level list that misses `cnt`
    ("mysens2", "myhdl", "not_proven", "guard_sim_synth_differ"),
    # Round 6: judged on the emitted Verilog, so how the Python was spelled does not matter
    ("myalias", "myhdl", "not_proven", "guard_sim_synth_differ"),  # `import always as alw`
    # A MyHDL project whose top returns any object with a `convert()` writes its core by hand: the
    # core is judged by its text, in the plain shape @always_comb writes or not at all
    ("mylab4", "myhdl", "not_proven", "guard_sim_synth_differ"),  # a nested label hides a read
    ("myfst", "myhdl", "not_proven", "guard_sim_synth_differ"),  # a static function
    # Round 8: a combinational block that reads a signal before it writes it (`y = t; t = cnt;`):
    # a simulator does not rerun the block for its own write. Hand-written (Verilator's
    # ALWCOMBORDER pass), and genuine MyHDL @always_comb (the block parser)
    ("hrbw", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 9: a loop that reads in one iteration what another writes (gloopit is genuine MyHDL;
    # mloopit writes the core by hand), no `@*` in a MyHDL core, and functions and tasks: a
    # function may read only its arguments, a task is never allowed
    ("gloopit", "myhdl", "not_proven", "guard_sim_synth_differ"),
    ("mloopit", "myhdl", "not_proven", "guard_sim_synth_differ"),
    ("mstarloop", "myhdl", "not_proven", "guard_sim_synth_differ"),
    ("vfunc", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("vtask2", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("vfsens", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("mrbw2", "myhdl", "not_proven", "guard_sim_synth_differ"),
    # Round 10: a function that reads a module signal while the old token scanner thought it was
    # a local or a parameter (a block label, a cast, a same-named localparam, a header
    # parameter list, a nested local, a `disable`)
    ("flabel", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("fcasti", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("fparam2", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("fdup", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("fport", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("fparam3", "verilog", "not_proven", "guard_sim_synth_differ"),
    # a constant that is x to Yosys and a simulator (`1/0`, `0 ** -1`) is folded by Verilator,
    # which then takes another generate branch (or module) than Yosys: the function and order
    # checks would judge a different design, so the check refuses
    ("divgen", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("divmod", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("powgen", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("divcomb", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("gencall", "verilog", "not_proven", "guard_sim_synth_differ"),  # an impure call in a branch
    ("gendot", "verilog", "not_proven", "guard_sim_synth_differ"),  # a hierarchical call g.rd()
    (
        "gendotx",
        "verilog",
        "not_proven",
        "guard_sim_synth_differ",
    ),  # the same, branch chosen by 1/0
    # Round 11: the elaboration cross-check must see every always block, whatever its keyword or
    # shape, and a function must not read a hierarchical name that one tool folds to a constant
    ("svcombgen", "verilog", "not_proven", "guard_sim_synth_differ"),  # always_comb, 1/0 branch
    ("svcombsub", "verilog", "not_proven", "guard_sim_synth_differ"),  # same, in a sub-module
    ("concatgen", "verilog", "not_proven", "guard_sim_synth_differ"),  # `{y, t} = {t, ui_in}`
    ("svescw", "verilog", "not_proven", "guard_sim_synth_differ"),  # `always_comb\y` and `\;`
    ("hierk", "verilog", "not_proven", "guard_sim_synth_differ"),  # function reads `g.k`
    # Round 12: a combinational block that can read a signal before it writes it on SOME path
    # (judged path by path on Verilator's tree: ALWCOMBORDER only sees the text order)
    ("c01brorder", "verilog", "not_proven", "guard_sim_synth_differ"),  # in one `if` branch
    ("c04caseorder", "verilog", "not_proven", "guard_sim_synth_differ"),  # in one case item
    ("c06namedlocalbr", "verilog", "not_proven", "guard_sim_synth_differ"),  # a named-block local
    ("c10combsv", "verilog", "not_proven", "guard_sim_synth_differ"),  # in an always_comb
    ("c13partbr", "verilog", "not_proven", "guard_sim_synth_differ"),  # half written on one path
    # vloopit: a loop that reads `t` in one pass and writes it in a later one (it used to be
    # pinned as not detected: Verilator's ALWCOMBORDER cannot see it, the path walk does)
    ("vloopit", "verilog", "not_proven", "guard_sim_synth_differ"),
    # a parameter with an x or z bit (`1/0`) is refused before anything is judged on it
    ("a04foldcomb", "verilog", "not_proven", "guard_sim_synth_differ"),  # picks the bad order
    ("e04splitcall", "verilog", "not_proven", "guard_sim_synth_differ"),  # `rd\n(ui_in)` too
    ("g07genxcase", "verilog", "not_proven", "guard_sim_synth_differ"),  # a generate `case` on x
    ("hiersplit", "verilog", "not_proven", "guard_sim_synth_differ"),  # `g\n.rd(ui_in)`
    # Round 13: the same with plain `assign`s only (no always block, function or task: the tree
    # checks used to be skipped), through a parameter override, `$clog2(1/0)` and `5 % 0`
    ("xgenassign", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("xsubover", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("xclog", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("xmodx", "verilog", "not_proven", "guard_sim_synth_differ"),
    # a function argument that is not an input: on one path of an `if` (Verilator's ternary), and
    # an inout read before the block writes it
    ("fnoutif", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("fninout", "verilog", "not_proven", "guard_sim_synth_differ"),
    # an unsized number of 2**31 or more: Verilator folds `2147483648 > 0` to false, Yosys and
    # Icarus to true, so the order and purity checks judged the branch the proof does not use
    ("bigfold", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("bigimpure", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Icarus ends a `//` comment at a lone CR, Yosys and Verilator do not
    ("crhide", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Verilator ends an attribute at a `*)` inside its string, Yosys does not
    ("attrhide", "verilog", "not_proven", "guard_sim_synth_differ"),
    # `unique case`: synthesis assumes what a simulator does not
    ("uniquecase", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 14: a generate condition that can be x (`1 / Z`, `g / Z`, `Z ** -1`, `$clog2(1 / Z)`
    # with Z = 0). Verilator takes the first branch of an x `if` and drops the condition, Icarus
    # and Yosys may take the other one, where a block reads before it writes or a function is
    # impure (judged on Verilator's tree before it evaluates the parameters)
    ("b03arr", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("b08genlp", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("x11genifbare", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("x07genforx", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("b06impure", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("x10clogif", "verilog", "not_proven", "guard_sim_synth_differ"),
    # a constant that Verilator folds to 0 and the simulator reads as x (judged on Icarus's
    # elaboration): a select outside the range in a localparam or in the generate `case` itself,
    # and a constant function that returns x
    ("xsel", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("xseld", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("qf2", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 15: a localparam from a constant function that selects outside the range, where an
    # `if (!x)` turns the x into a defined value: Icarus gives 1, Verilator and Yosys give 0. The
    # value steers an array index (c02ifnot), an `if` on the output pins (e05genlaunder) and a
    # parameter override of a sub-module (c14sub); judged by comparing every parameter's value
    ("c02ifnot", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("e05genlaunder", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("c14sub", "verilog", "not_proven", "guard_sim_synth_differ"),
    # the same function called inline (not in a parameter, so no value is compared): with
    # constant arguments as a case label, inside another function, in a clocked `if` and as a
    # part-select base; and with a signal and the constant 6 (Yosys warns once it inlines it)
    ("inlcase", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlfn", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlff", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlpart", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlsig", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Icarus 12 aborts on a localparam with two packed dimensions: still refused
    ("icapack", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 16: the laundered value swapped between loop passes, instances and overrides (the
    # same set of values in both tools), wider in Icarus (2'b11 against 1'h1: the bit both hold
    # agrees) in a generate condition and a defparam, in a real localparam (whose value is not
    # compared), in a localparam's range, and in every element of an instance array; and untyped
    # localparams that overflow 32 bits, which Icarus makes wider (4294967295 against -1)
    ("swaploop", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("swapinst", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("swapover", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("widegen", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("widedefp", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("realcall", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("realcallu", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("rangecall", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("arrpick", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("ovfmask", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 16: inline constant calls whose select is not proven inside its range (a select that
    # proves nothing lets the call through, see s17, s46, e4, frev and fcnt below): a 3-bit index
    # into a 4-bit argument, a loop to `i <= 8` over 8 bits, an index `i - 1` from i = 0, and a
    # signed index
    ("inlpick3", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlle8", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlim1", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("inlsgn", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 17: an index that round 16 proved from a constant select that is not an operand: an
    # element of a local array (`v[m[0]]`, as wide as its type, not one bit) in an `if`, in a
    # reset value, as a case label and inside another function; and a part-select of a local
    # that one path leaves unset (`v[loc[1:0]]`, x)
    ("ua04", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("ua08", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("arrelc", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("arrelf", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("locsel2", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 18: a real that is infinite or NaN as the argument of a constant call whose select is
    # proven: a simulator converts it to an all-x integer (a literal, a NaN localparam steering an
    # always block, an untyped child parameter that an override sets to a real; ri09 below)
    ("ri02", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("ri04", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("ri11", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Round 19: an infinite real that Yosys converts to an all-x integer outside a constant call
    # (stored by a clocked block, assigned onto a wire that is a case label), and a lint_off list
    # of codes that switches off BLKSEQ (xrefreal, realbody and realsafe are below)
    ("realff", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("realcase", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("lolist", "verilog", "not_proven", "guard_sim_synth_differ"),
]


@pytest.mark.parametrize(
    ("design", "fe", "verdict", "reason"), RTL_SEEDS, ids=[f"{d}-{f}" for d, f, _, _ in RTL_SEEDS]
)
async def test_equiv_rtl_seeds(
    server: Client, tmp_path: Path, design: str, fe: str, verdict: str, reason: str
) -> None:
    async with server as client:
        await design_project(client, tmp_path, design, fe)
        result = await call(client, "check_equivalence", project=design)
        details = result["details"]
        assert "verdict" in details, result["summary"]
        print(f"\n{design}-{fe}: {details['verdict']} {details['reason']}: {details['message']}")
        assert result["ok"] is False, result["summary"]
        assert (details["verdict"], details["reason"]) == (verdict, reason), details
        states = step_states(await call(client, "status", project=design))
        assert states["equivalence"] == "fail"


async def test_equiv_initial_value_declaration_stops_at_lint(
    server: Client, tmp_path: Path
) -> None:
    """`reg cnt = 0` is refused by the lint step before any proof; it is never a pass."""
    async with server as client:
        project = await design_project(client, tmp_path, "initz", "verilog")
        text = (DESIGNS / "initz" / "verilog_decl.v").read_text()
        (project / "src" / "initz.v").write_text(text)
        result = await call(client, "check_equivalence", project="initz")
        assert result["ok"] is False
        assert "PROCASSINIT" in result["summary"]
        assert result["details"].get("verdict") is None
        states = step_states(await call(client, "status", project="initz"))
        assert states["equivalence"] != "pass"


async def test_an_always_comb_that_reads_before_it_writes_stops_at_lint(
    server: Client, tmp_path: Path
) -> None:
    """combsv: no `always @*` in the file, only an `always_comb` that reads `t` before it writes
    it. The emit lint (Verilator ALWCOMBORDER) already refuses it before any proof, so the
    always_comb order pass of the check does not need to look at `always_comb` blocks."""
    async with server as client:
        await design_project(client, tmp_path, "combsv", "verilog")
        result = await call(client, "check_equivalence", project="combsv")
        assert result["ok"] is False
        assert "ALWCOMBORDER" in result["summary"]
        assert result["details"].get("verdict") is None
        states = step_states(await call(client, "status", project="combsv"))
        assert states["equivalence"] != "pass"


@pytest.mark.parametrize("fe", ["verilog", "amaranth"])
async def test_equiv_async_reset_counter_with_a_start_value_is_equivalent(
    server: Client, tmp_path: Path, fe: str
) -> None:
    """A counter with an asynchronous reset and the start value 0: the initial-value check
    (stage 2) runs on it (`initdep` in the timings) and must not refuse it."""
    async with server as client:
        await design_project(client, tmp_path, "arcnt", fe)
        result = await call(client, "check_equivalence", project="arcnt")
        details = result["details"]
        assert result["ok"] is True, result["summary"]
        assert details["verdict"] == "equivalent" and "initdep" in details["seconds"]


async def test_equiv_a_memory_that_starts_with_contents_is_explained(
    server: Client, tmp_path: Path
) -> None:
    async with server as client:
        await design_project(client, tmp_path, "ammem", "amaranth")
        result = await call(client, "check_equivalence", project="ammem")
        details = result["details"]
        assert result["ok"] is False and details["verdict"] == "not_equivalent"
        assert details["reason"] == "rtl_initial_value"
        assert "memory's contents cannot be set by rst_n" in details["message"]


EXPOSE_PROBE = """
import json, shutil, subprocess
from pathlib import Path
from siliconsmith.worker import equiv_parse, equiv_script

work = Path("/work/build/expose_probe")
work.mkdir(parents=True, exist_ok=True)
for f in ("project.v", "arcnt.v"):
    shutil.copy("/work/src/" + f, work / f)
real = equiv_script.state_check("arcnt")
# the same script without the `expose -dff` lines: the guard behind them must fire
broken = real.replace("\\nexpose -dff\\n", "\\n")
out = {}
for tag, script in (("real", real), ("broken", broken)):
    (work / f"{tag}.ys").write_text(script)
    run = subprocess.run(
        ["yosys", "-q", "-l", f"{tag}.log", "-s", f"{tag}.ys"], cwd=work, capture_output=True
    )
    log = (work / f"{tag}.log").read_text()
    out[tag] = [run.returncode, equiv_parse.state_result(log), equiv_script.M_EXPOSE in log]
print(json.dumps(out))
"""


async def test_equiv_the_expose_guard_runs_and_fails_closed(
    server: Client, tmp_path: Path, image: str
) -> None:
    """GUARD_EXPOSE is part of the state check: with `expose -dff` the state check of an
    async-reset counter runs through; without it the guard stops the script (`unusable`)."""
    async with server as client:
        project = await design_project(client, tmp_path, "arcnt", "verilog")
        await call(client, "check_equivalence", project="arcnt")  # emits src/project.v
        proc = run_hardened(image, project, "python", "-c", EXPOSE_PROBE, timeout=300)
        assert proc.returncode == 0, proc.stderr
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        assert got["real"][0] == 0 and got["real"][1] == "equal" and got["real"][2] is True
        assert got["broken"][0] != 0 and got["broken"][1] == "unusable"


GOLD_PROBE = """
import json, subprocess
from pathlib import Path
from siliconsmith.worker import equiv_script, equiv_sv

work = Path("/work/build/gold_probe")
work.mkdir(parents=True, exist_ok=True)
for f in ("project.v", "segsync.v"):
    text = Path("/work/src/" + f).read_text("latin-1")
    (work / f).write_text(equiv_sv.clean(text), "latin-1")
out = {}
for mode in ("proof", "rtl", "rtl_noinit"):
    real = equiv_script.gold("segsync", mode)
    # the same script with Yosys's ROM step: the table becomes a memory
    rom = real.replace("\\nproc -norom\\n", "\\nproc\\n")
    for tag, script in ((mode, real), (mode + "-rom", rom)):
        (work / f"{tag}.ys").write_text(script)
        run = subprocess.run(
            ["yosys", "-q", "-l", f"{tag}.log", "-s", f"{tag}.ys"], cwd=work, capture_output=True
        )
        log = (work / f"{tag}.log").read_text()
        out[tag] = [run.returncode, equiv_script.M_OK in log, "problems in 'check -assert'" in log]
    out[mode].append(rom != real)
print(json.dumps(out))
"""


async def test_a_registered_table_with_a_constant_bit_is_a_sound_model_of_the_rtl(
    server: Client, tmp_path: Path, image: str
) -> None:
    """segsync (round 14 v66): a seven-segment `case` registered with a sync reset, bit 7 is 0 in
    every entry. As a ROM, `memory` merged the output register into it and dropped the data input
    of the constant bit, so `check -assert` refused the RTL side (`error structure`). The RTL side
    keeps the `case` as logic (`proc -norom`): every mode of it passes the structure guard, and
    the ROM version of the proof mode still fails it (so this probe sees the difference). The whole
    check runs in `test_equiv_normal_designs_are_equivalent`."""
    async with server as client:
        project = await design_project(client, tmp_path, "segsync", "verilog")
        emitted = await call(client, "emit_verilog", project="segsync")
        assert emitted["ok"] is True, emitted["summary"]
        proc = run_hardened(image, project, "python", "-c", GOLD_PROBE, timeout=300)
        assert proc.returncode == 0, proc.stderr
        got = json.loads(proc.stdout.strip().splitlines()[-1])
        for mode in ("proof", "rtl", "rtl_noinit"):
            assert got[mode] == [0, True, False, True], (mode, got)
        assert got["proof-rom"][0] != 0 and got["proof-rom"][2] is True, got


@pytest.mark.parametrize("fe", ["verilog", "amaranth"])
async def test_a_registered_table_with_a_constant_bit_is_equivalent(
    server: Client, tmp_path: Path, fe: str
) -> None:
    """segrom (round 14 v69 and a13): a seven-segment table registered on an enable, bit 7 is 0
    in every entry (a `case` in Verilog, an Array of constants in Amaranth). It used to be
    `error structure` (see the probe above); the RTL side keeps the table as logic now."""
    async with server as client:
        await design_project(client, tmp_path, "segrom", fe)
        result = await call(client, "check_equivalence", project="segrom")
        details = result["details"]
        print(f"\nsegrom-{fe}: {details.get('verdict')} {details.get('method')}")
        assert result["ok"] is True, result["summary"]
        assert details["verdict"] == "equivalent" and details["reason"] is None, details


# Designs that must NOT be refused by the sim/synth guard, on every front-end they have: the
# Verilog that the tool emits (and the hand-written core of a Verilog project) is read exactly
# as the check reads it. A rule that fires on normal front-end output is a bug.
PRECISION = [
    "ammem", "arcnt", "carst", "deepinit", "fallingedge", "initz", "seqdet", "shiftpar",
    # MyHDL `@always_comb` in the shapes MyHDL writes: if chains, local variables, slices, a
    # list of signals, a for loop, an enum
    "mycomb", "mylocals", "mylists", "myfor", "mymix",
]  # fmt: skip


PRECISION_CASES = [
    (d, fe) for d in PRECISION for fe in FRONTENDS if (DESIGNS / d / SOURCE_FILE[fe]).exists()
]


@pytest.mark.parametrize(("design", "fe"), PRECISION_CASES)
async def test_the_refusals_do_not_fire_on_emitted_rtl(
    server: Client, tmp_path: Path, design: str, fe: str
) -> None:
    async with server as client:
        project = await design_project(client, tmp_path, design, fe)
        emitted = await call(client, "emit_verilog", project=design)
        assert emitted["ok"] is True, emitted["summary"]
        files = [(f, (project / "src" / f).read_text()) for f in ("project.v", f"{design}.v")]
        core = {f"{design}.v"}
        hand, myhdl = (core, set()) if fe != "myhdl" else (set(), core)
        assert equiv_sv.refusal(files, hand, myhdl) is None, (design, fe)


def comb_order_in_container(image: str, project: Path, name: str) -> str:
    """What `equiv._comb_order` says about the project's emitted RTL (`src/`), run in the
    container where Verilator is: "None" when no combinational block reads before it writes."""
    script = (
        "from pathlib import Path\n"
        "from siliconsmith.worker import equiv\n"
        f"name = {name!r}\n"
        "src = Path('/work/src')\n"
        "files = ('project.v', name + '.v')\n"
        "sources = [(f, (src / f).read_bytes().decode('latin-1')) for f in files]\n"
        "print('RESULT', equiv._comb_order(Path('/work/build/cc'), sources, name))\n"
    )
    proc = run_hardened(image, project, "python", "-c", script, timeout=300)
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT "))
    return line.removeprefix("RESULT ")


@pytest.mark.parametrize(("design", "fe"), PRECISION_CASES)
async def test_no_combinational_read_before_write_on_emitted_rtl(
    server: Client, tmp_path: Path, image: str, design: str, fe: str
) -> None:
    """The ALWCOMBORDER pass (every `always @*` linted as `always_comb`) adds no refusal to the
    designs that must pass, on all three front-ends."""
    async with server as client:
        project = await design_project(client, tmp_path, design, fe)
        emitted = await call(client, "emit_verilog", project=design)
        assert emitted["ok"] is True, emitted["summary"]
        assert comb_order_in_container(image, project, design) == "None", (design, fe)


@pytest.mark.parametrize("fe", FRONTENDS)
async def test_no_combinational_read_before_write_in_the_counter(
    server: Client, tmp_path: Path, image: str, fe: str
) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, fe)
        emitted = await call(client, "emit_verilog", project="counter")
        assert emitted["ok"] is True, emitted["summary"]
        assert comb_order_in_container(image, project, "counter") == "None", fe


# Normal designs that must be proven equivalent, on every front-end they have. swpick: an output
# picked by a 4-way switch from a register; the old power-up run spread x through the netlist
# gate by gate and called it "NOT equivalent", then "not proven". n52: sub-module instances joined
# by a wire array (`.q(stage[0])`, a process Yosys anchors at the port). n45: a function that
# reads the members of a packed struct argument (`p.hi`). pieces: combinational blocks built
# piece by piece before they are read (constant selects, full case and casez without default,
# a loop accumulator, a named-block local, array elements, struct members). Round 14: seg7reg
# (a seven-segment decoder registered every clock: a Verilog `case`, an Amaranth Array, a MyHDL
# tuple), seg7en (the same on an enable: a Verilog `case`, an Amaranth Switch) and segsync (the
# 8-bit table with a constant bit): the step-by-step search from reset stalls on them, the
# one-shot search proves them. structport: a struct argument `cfg` and a port `.cfg(cfg)`.
# Round 15: c12inrange (the control for c02ifnot: the same constant function with every select
# in range, so every tool gives the same value); blinkmux and seg7mux (a multiplexer whose select
# is a flip-flop with no value before reset: the run from power-up used to read the netlist side
# through the RTL's values, saw x on a pin the netlist defines and called it "NOT equivalent").
# Round 15: forint, forpat and forfunc (a loop index declared as `int` in the for header, in an
# always_comb and in a function: it belongs to the loop, so the two-state type is accepted there);
# inlok (the control of the inline-call seeds: constant calls of functions without a computed
# index, and the function with one called with a signal or in a localparam).
# Round 16: arrinv, arrbit, arradd and arrnest (an instance array of a module with a parameter or
# localparam, also nested: Verilator keeps the array as one instance, a simulator makes one per
# element, and each element must be counted to compare the values). ovfmaskok (the control of
# ovfmask: the same masks with a size, so every tool gives them the same 32-bit value).
# Round 16: s17, s46, e4, frev and fcnt (review designs with inline constant calls of functions
# whose every select is provably inside its range: a simple for loop over a declared width, a
# 2-bit index into 4 elements, a parameter with one agreed value), n09 (a PWM whose search from
# reset took 63 s step by step: the two searches now race), and aluok (the workaround form of the
# Yosys 0.62 ALU bug in alubug: the signed compare written from the sign bits).
# Round 18: realok (the control of ri09: a proven constant call with plain integer parameters,
# typed, untyped and set by an override) and mr4dbp (the control of mr4db: the same call cycle
# with every select proven).
# Round 19: realfin (the control of realcase: a finite real, the same number in both tools) and
# deadlp, deadkid and deadcase (a localparam, a child's parameter and a generate case localparam
# passed to a helper with a proven select in code that no tool builds: dead code everywhere).
# Round 20: strhelp (a sized string localparam and a child's sized string parameter set by an
# override, passed to helpers in plain assigns: they hold bits, never a real).
# Round 21: lofoldn (the control of lofold and locut: lint_off comments that Verilator reads as
# the other tools do), longok (a 127-character parameter name, which Verilator keeps), and
# flocal, flocalg, flocalu (an untyped localparam inside a constant helper, a generate-pass helper
# and an unused function, passed on with the function's argument: only the simulator lists it,
# with a plain number, so it holds no real; all three were refused at bf5eb0a).
# Round 22: lonote and lohdr (a taught-style counter and a registered adder whose prose block
# comment only mentions a lint_off, then a latch or BLKSEQ: Verilator reads a comment as a
# meta-comment only when it starts with `verilator`; both were refused at 70f0f4e).
# Round 23: floclit (a localparam inside a function, whose call sets a wire's range, written as
# one sized number: every tool reads it the same, so it needs no comparison) and wint (untyped
# localparams computed with an unsized number, W - 1 and W * 2, which Icarus makes wider than
# 32 bits, used as a counter limit and a range, and 8'hff + 8'h00 with a size in a
# concatenation: the control of wcat and wsum).
# Round 25: k07c (the control of k07 and kd4: LAST + 1, a typed localparam computed first, and
# {4'd0, LAST} + 8'd1 as function arguments, which every tool works out at the input's width).
# Round 26 (all refused at 97f3bdb by the narrow-argument rule): clogarg (maxi(1, $clog2(DEPTH))),
# enumarg (enum items as function arguments), multiw (a module used twice whose function's input
# width and argument TOP - 1'b1 depend on the copy's W) and multicast (AW'(DEPTH - 1) in a module
# used with two depths).
# Round 27 (both refused at 6da0a89 as a width the check could not work out): enumcast
# (dec(mode_t'(ui_in[1:0])), a cast to a 2-bit enum) and clogrange (rotl(ui_in, i + 1) with
# `input [$clog2(N)-1:0] n`).
EQUIVALENT = [
    "swpick",
    "n52",
    "n45",
    "pieces",
    "seg7reg",
    "seg7en",
    "segsync",
    "structport",
    "c12inrange",
    "blinkmux",
    "seg7mux",
    "forint",
    "forpat",
    "forfunc",
    "inlok",
    "arrinv",
    "arrbit",
    "arradd",
    "arrnest",
    "ovfmaskok",
    "s17",
    "s46",
    "e4",
    "frev",
    "fcnt",
    "n09",
    "aluok",
    "realok",
    "mr4dbp",
    "realfin",
    "deadlp",
    "deadkid",
    "deadcase",
    "strhelp",
    "lofoldn",
    "longok",
    "flocal",
    "flocalg",
    "flocalu",
    "lonote",
    "lohdr",
    "floclit",
    "wint",
    "k07c",
    "clogarg",
    "enumarg",
    "multiw",
    "multicast",
    "enumcast",
    "clogrange",
]
EQUIVALENT_CASES = [
    (d, fe) for d in EQUIVALENT for fe in FRONTENDS if (DESIGNS / d / SOURCE_FILE[fe]).exists()
]


@pytest.mark.parametrize(
    ("design", "fe"), EQUIVALENT_CASES, ids=[f"{d}-{f}" for d, f in EQUIVALENT_CASES]
)
async def test_equiv_normal_designs_are_equivalent(
    server: Client, tmp_path: Path, design: str, fe: str
) -> None:
    async with server as client:
        await design_project(client, tmp_path, design, fe)
        result = await call(client, "check_equivalence", project=design)
        details = result["details"]
        print(f"\n{design}-{fe}: {details.get('verdict')} {details.get('reason')}")
        assert result["ok"] is True, result["summary"]
        assert details["verdict"] == "equivalent" and details["method"] == "equiv_induct", details
        assert "tree" in details["seconds"], details["seconds"]  # the tree checks ran


# What each refusal says to write instead (round 15), and where the values differ (round 16: the
# instance or loop pass, as a simulator names it).
REFUSAL_ADVICE = {
    "inlcase": "Compute it in a localparam first",
    "inlfn": "Compute it in a localparam first",
    "inlsig": "Keep every select inside the declared range",
    "icapack": "declare it as a plain vector",
    "realcall": "Compute the call in an integer localparam first",
    # round 23: the call in the range gives the localparam another width in each tool (refused
    # since then by the width comparison, before the call itself is named)
    "rangecall": "(rangecall.v, line 16) gives the parameter `Q` the same value but 2 unsigned "
    "bits in a simulator (Icarus) and 1 unsigned bit in Verilator's elaboration",
    "swaploop": "the value 1 in a simulator (Icarus) but 0 in Verilator's elaboration (in "
    "`tt_um_swaploop.core.b[0]`)",
    "swapinst": "(in `tt_um_swapinst.core.u0`)",
    "widegen": "the value 3 in a simulator (Icarus) but 1 in Verilator's elaboration",
    "arrpick": "(in `tt_um_arrpick.core.u[0]`)",
    "ovfmask": "give a localparam that can overflow a type or a size",
    # Round 18: an infinite real cast to an integer as an argument; a call cycle that hid a
    # select (inline, and in a real localparam whose value is not compared)
    "ri09": "a real: a simulator converts an infinite or NaN real to an all-x integer",
    "mr4db": "calls the function `g` with constant arguments",
    "vc03": "gives the parameter `RR` a value with a call of a function that can give",
    # Round 19: a real localparam read by a hierarchical name in a proven call, an infinite real
    # converted in a function body, and a real argument of a function that has no select
    "xrefreal": "a hierarchical name",
    "realbody": "Use a real only to compute an integer parameter or localparam",
    "realsafe": "Compute an integer localparam from the real first",
    # Round 20: an infinite real argument that Yosys converts without a warning in a localparam
    # value, a parameter override, an unrolled loop and a function that it works out; a back-tick
    # or a double quote inside an escaped identifier (Icarus expands a macro there; Verilator and
    # Icarus spell the quote differently, so a value was never compared)
    "realparam": "with an argument that can hold a real",
    "realover": "also in a parameter value, an override",
    "realloop": "calls the function `f` with an argument that can hold a real",
    "realnest": "(realnest.v, line 21) calls the function `f`",
    "esctick": "(esctick.v, line 12) uses a back-tick inside an escaped identifier",
    "escquote": "(escquote.v, line 13) uses a double quote inside an escaped identifier",
    # Round 21: lint_off comments read the way Verilator reads them (it deletes every `//` in a
    # comment; it ends a `// verilator lint_off` comment at its first `/` and lints the rest of
    # the line, here a hidden race: Icarus and the gates differ from cycle 2), parameter names
    # that Verilator hashes (their values were never compared), and an infinite real declared
    # inside a function, which still counts as a real (the control of flocal)
    "lofold": "(lofold.v, line 14) uses `verilator lint_off BLKSEQ`",
    "locut": "(locut.v, line 16) uses a `// verilator lint_off` (or lint_on, lint_save, "
    "lint_restore) comment that goes on after a single `/`",
    "longname": "longname.v, line 14: Verilator shortens a parameter name of 128 characters",
    "longesc": "longesc.v, line 12: Verilator shortens a parameter name of 128 characters",
    "flocalreal": "(flocalreal.v, line 21) calls the function `f` with an argument that can hold "
    "a real",
    # Round 23: a localparam inside a function that Verilator only works out as a constant (the
    # call sets a wire's range), so only the simulator lists it and its value was never
    # compared: an overflowing shift, the PLAN.md overflow 8'hff + 8'h01 and $bits of the
    # argument gave the wire another width in Icarus than in the gates (all three equivalent at
    # 7127c03); and the control, the same shift at module level, compared and refused as before
    "flocshift": "(flocshift.v, line 17) declares the localparam `M` inside the function `h`",
    "flocovf": "(flocovf.v, line 16) declares the localparam `M` inside the function `h`",
    "flocbits": "(flocbits.v, line 16) declares the localparam `W` inside the function `h`",
    "flocmod": "(flocmod.v, line 12) gives the parameter `M` the value 4294967296 in a simulator "
    "(Icarus) but 0 in Verilator's elaboration",
    # Round 23: an untyped localparam of sized numbers has the same value in every tool but is
    # wider in Icarus (lossless): a concatenation and $bits saw the width (Icarus RTL and gates
    # differ; both equivalent before widths were compared)
    "wcat": "(wcat.v, line 14) gives the parameter `M` the same value but 9 unsigned bits in a "
    "simulator (Icarus) and 8 unsigned bits in Verilator's elaboration and the gates",
    "wsum": "(wsum.v, line 12) gives the parameter `S` the same value but 5 unsigned bits in a "
    "simulator (Icarus) and 4 unsigned bits",
    # Round 24 (all equivalent at d820087, with Icarus RTL and gates differing): an unsized signed
    # hex number, -8 in Icarus and +8 in Yosys, in a function's localparam that Verilator's tree
    # shows as the plain 8 and in a signal sum; an unsized hex number whose digits need 36 bits
    # (32 in Icarus and Verilator, 36 in Yosys: $bits differs); $countbits, a signed int in
    # Icarus and Verilator but unsigned in Yosys 0.62 (a generate if takes another branch); a
    # function local in two module copies, of which Verilator lists only the one that calls the
    # function at run time (both copies' values agreed there); and an untyped sum of two sized
    # 40-bit numbers, 41 bits in Icarus and 40 in Verilator (only exactly 32 bits passes now)
    "sbloc": "(sbloc.v, line 15) uses the unsized signed number `'sh8`",
    "sbsig": "(sbsig.v, line 13) uses the unsized signed number `'sh8`",
    "longhex": "(longhex.v, line 13) uses the unsized number `'h0_0000_000F`, whose digits need "
    "more than 32 bits",
    "cntbits": "(cntbits.v, line 14) uses `$countbits`, whose result Yosys 0.62 reads as unsigned",
    "fpart3": "(fpart3.v, line 14) declares the localparam `M` inside the function `f`",
    "fcopies": "(fcopies.v, line 15) declares the localparam `M` inside the function `f`",
    "w40sum": "(w40sum.v, line 14) gives the parameter `M` the same value but 41 unsigned bits in "
    "a simulator (Icarus) and 40 unsigned bits",
    # Round 25: a constant function argument narrower than the input, which Yosys 0.62 works out
    # at its own width (16 in a simulator, 0 in the gates): a localparam that a generate `if`
    # reads, a localparam that divides, a parameter override
    "k07": "(k07.v, line 24) calls the function `clog2` with an argument that is 4 bits wide, "
    "narrower than the function's input (32 bits)",
    "kd4": "(kd4.v, line 17) calls the function `f` with an argument that is 4 bits wide, "
    "narrower than the function's input (8 bits)",
    "kp1": "(kp1.v, line 22) calls the function `f` with an argument that is 4 bits wide, "
    "narrower than the function's input (8 bits)",
    # Round 26 (both equivalent at 97f3bdb, with Icarus RTL and gates differing): $signed(4'h8)
    # inside a narrow argument is -8, not 8, so the 8-bit sum overflows in Yosys 0.62; a loop
    # that starts from a 4-bit localparam gives an integer loop variable 4 bits in Yosys 0.62
    "sgnarg": "(sgnarg.v, line 18) calls the function `g` with an argument that is 8 bits wide, "
    "narrower than the function's input (16 bits), and a part of it that reads no signal "
    "(numbers, parameters, genvars, a `for` loop's variable or a function's own variables) holds "
    "`-`, whose value",
    "loopst": "(loopst.v, line 25) calls the function `f` with an argument that is 4 bits wide, "
    "narrower than the function's input (8 bits)",
    # Round 27 (genst and typeparam equivalent at 6da0a89, with Icarus RTL and gates differing):
    # a generate loop whose genvar starts from a 4-bit localparam (Yosys 0.62 makes the genvar
    # and a localparam made from it 4 bits wide), a localparam of a typedef type whose value
    # Yosys 0.62 works out at its own 4 bits; and enumbad, a carry into a wider enum cast
    # (refused at 6da0a89 as a width the check could not work out, now for the carry)
    "genst": "(genst.v, line 23) starts a generate `for` loop of the genvar `g` from a value that "
    "is 4 bits wide",
    "typeparam": "(typeparam.v, line 18) gives the parameter `K`, whose type is a typedef, a "
    "value that is 4 bits wide, narrower than its type (8 bits)",
    "enumbad": "(enumbad.v, line 19) casts a value that is 2 bits wide, narrower than the cast "
    "(3 bits)",
}


@pytest.mark.parametrize("design", sorted(REFUSAL_ADVICE))
async def test_an_inline_call_or_an_icarus_crash_is_refused_with_what_to_write_instead(
    server: Client, tmp_path: Path, design: str
) -> None:
    async with server as client:
        await design_project(client, tmp_path, design, "verilog")
        result = await call(client, "check_equivalence", project=design)
        details = result["details"]
        print(f"\n{design}: {details.get('verdict')} {details.get('reason')}: {details['message']}")
        assert (details["verdict"], details["reason"]) == ("not_proven", "guard_sim_synth_differ")
        assert REFUSAL_ADVICE[design] in details["message"], details["message"]
        if design == "icapack":  # the assertion line itself, and whose limit it is
            assert "assert: netmisc.cc" in details["message"]
            assert "limit of the simulator" in details["message"]
            assert "Aborted" not in details["message"]


async def test_the_one_shot_search_from_reset_proves_a_registered_table(
    server: Client, tmp_path: Path, image: str
) -> None:
    """seg7reg (round 14 v62): the induction proof closes, and the check from reset is decided by
    the one-shot search (one `sat` problem of 32 cycles). Both searches start at once (round 16);
    the step-by-step search, which stalls on this design after about 20 cycles, is stopped when
    the one-shot proof ends. The one-shot log shows the whole problem: 33 time steps, the
    per-side power-up and reset at cycle 1."""
    async with server as client:
        project = await design_project(client, tmp_path, "seg7reg", "verilog")
        synthesized = await call(client, "synthesize", project="seg7reg")
        assert synthesized["ok"] is True, synthesized["summary"]
    verdict = check_in_container(image, project, "/work/build/synth/tt_um_seg7reg.v")
    print(f"\nseg7reg: {verdict['verdict']} {verdict['seconds']}")
    assert verdict["verdict"] == "equivalent" and verdict["ok"] is True, verdict["message"]
    assert verdict["cycles_covered"] == 32 and verdict["method"] == "equiv_induct"
    scratch = project / "build" / "eq_scratch"
    once = (scratch / "bmc_once.log").read_text()
    assert once.count("SAT proof finished - no model found: SUCCESS!") == 1
    assert "Setting up time step 33:" in once and "Setting up time step 34:" not in once
    assert "Import all_undef constraint for this timestep: \\ff_gold" in once
    assert "Import def constraint for this timestep: \\ff_gate" in once
    assert (scratch / "bmc.ys").exists()  # the step-by-step search started too ...
    step = scratch / "bmc.log"
    step_log = step.read_text() if step.exists() else ""
    assert "proved base case for 32 steps: SUCCESS" not in step_log  # ... and was stopped


async def test_a_known_yosys_bug_in_an_alu_is_not_equivalent_and_the_workaround_is_equivalent(
    server: Client, tmp_path: Path
) -> None:
    """Round 16 (review design s06): Yosys 0.62, the version LibreLane and Tiny Tapeout pin,
    builds wrong gates for an unsigned subtract wider than its operands and a signed compare of
    the same operands, in one case here (also in an if chain, a ?: or separate assigns; its
    `alumacc` pass; fixed in Yosys 0.68). The RTL is right, so the check must say
    not_equivalent and name the output; aluok, the same ALU with the compare written from the
    sign bits, is in EQUIVALENT."""
    async with server as client:
        await design_project(client, tmp_path, "alubug", "verilog")
        result = await call(client, "check_equivalence", project="alubug")
    details = result["details"]
    print(f"\nalubug: {details.get('verdict')} {details.get('reason')}: {details['message']}")
    assert result["ok"] is False and details["verdict"] == "not_equivalent", details
    assert "uo_out[" in details["message"], details["message"]
    mismatch = details["first_mismatch"]
    assert any(d["signal"].startswith("uo_out[") for d in mismatch["differs"]), mismatch


async def test_a_display_mux_with_one_cell_changed_is_still_not_equivalent(
    server: Client, tmp_path: Path, image: str
) -> None:
    """Round 15: the control for seg7mux. The run from power-up now reads each side from its own
    copy of the proof model, so a netlist that really differs (one cell of the table changed to
    another function) must still be found, with a defined value on each side of the pin."""
    async with server as client:
        project = await design_project(client, tmp_path, "seg7mux", "verilog")
        synthesized = await call(client, "synthesize", project="seg7mux")
        assert synthesized["ok"] is True, synthesized["summary"]
    original = (project / "build" / "synth" / "tt_um_seg7mux.v").read_text()
    edited, what = swap_cell_type(original)
    (project / "build" / "edited.v").write_text(edited)
    verdict = check_in_container(image, project, "/work/build/edited.v")
    print(f"\nseg7mux ({what}): {verdict['verdict']} {verdict['reason']}: {verdict['message']}")
    assert verdict["verdict"] == "not_equivalent" and verdict["ok"] is False, verdict
    mismatch = verdict["first_mismatch"]
    assert any(d["signal"].startswith("uo_out[") for d in mismatch["differs"]), mismatch
    diff = mismatch["differs"][0]
    assert {diff["rtl"], diff["netlist"]} <= {"0", "1"} and diff["rtl"] != diff["netlist"]


async def test_an_x_that_only_spreads_through_the_netlist_is_never_not_equivalent(
    server: Client, tmp_path: Path
) -> None:
    """Round 13 n23v: a correct state machine (stage 1 leaves the shift register unproven, so the
    k-induction runs) plus an unreset mode bit `c` that picks between two input pins. With every
    flip-flop x, the mux shows x where every real power-up state gives the same value; the
    per-side search finds no difference, so it is not proven (it used to be "NOT equivalent")."""
    async with server as client:
        await design_project(client, tmp_path, "n23v", "verilog")
        result = await call(client, "check_equivalence", project="n23v")
        details = result["details"]
        print(f"\nn23v: {details.get('verdict')} {details.get('reason')}: {details.get('message')}")
        assert details["verdict"] == "not_proven", details
        assert details["reason"] in ("unproven_points", "time_limit"), details
        assert details["first_mismatch"] is None, details


# -- the function-purity check on Verilator's syntax tree (round 10) -----------------------------

# The bypasses of the old token scanner, and the message that names the signal read.
IMPURE_FIXTURES = {
    "flabel": "reads `cnt`",  # a block label named cnt
    "fcasti": "reads `cnt`",  # a cast around the read
    "fparam2": "reads `cnt`",  # a localparam cnt in another module
    "fdup": "reads `cnt`",  # a same-named function in another module
    "fport": "reads `ui_in`",  # a parameter list in the module header
    "fparam3": "reads `cnt`",  # a localparam cnt in another function
}


TREE_RUN = (
    "import time\n"
    "from siliconsmith.worker import equiv\n"
    "def tree_checks(folder, sources, name):\n"
    "    run = equiv._Run(folder, equiv.run_yosys, equiv.Clock(300, 600), time.monotonic())\n"
    "    got = equiv._tree_checks(run, sources, name)\n"
    "    return None if got is None else (got['reason'], got['message'])\n"
)


def purity_in_container(image: str, project: Path, name: str) -> str:
    """What `equiv._tree_checks` says about the project's emitted RTL (`src/`), run in the
    container where Verilator is: "None" when every function is pure."""
    script = (
        "from pathlib import Path\n"
        f"{TREE_RUN}"
        f"name = {name!r}\n"
        "src = Path('/work/src')\n"
        "files = ('project.v', name + '.v')\n"
        "sources = [(f, (src / f).read_bytes().decode('latin-1')) for f in files]\n"
        "print('RESULT', tree_checks(Path('/work/build/pu'), sources, name))\n"
    )
    proc = run_hardened(image, project, "python", "-c", script, timeout=300)
    assert proc.returncode == 0, proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT "))
    return line.removeprefix("RESULT ")


@pytest.mark.parametrize("design", ["fparam3"])  # the others do not pass emit_verilog's lint
async def test_the_purity_check_names_the_signal_a_function_reads(
    server: Client, tmp_path: Path, image: str, design: str
) -> None:
    async with server as client:
        project = await design_project(client, tmp_path, design, "verilog")
        emitted = await call(client, "emit_verilog", project=design)
        if emitted["ok"] is not True:  # the front end itself rejects it: even better
            print(f"\n{design}: emit_verilog says {emitted['summary']}")
            return
        got = purity_in_container(image, project, design)
        assert IMPURE_FIXTURES[design] in got and f"{design}.v, line" in got, got
        assert got.startswith("('guard_sim_synth_differ'"), got


def texts_in_container(image: str, project: Path, cases: dict[str, str]) -> dict[str, str]:
    """`equiv._tree_checks` on each text (a whole `tt_um_chip` module, in `chip.v`)."""
    script = (
        "import json\nfrom pathlib import Path\n"
        f"{TREE_RUN}"
        f"cases = json.loads({json.dumps(cases)!r})\n"
        "for i, (key, text) in enumerate(cases.items()):\n"
        "    sources = [('project.v', '// wrapper\\n'), ('chip.v', text)]\n"
        "    got = tree_checks(Path(f'/work/build/pu{i}'), sources, 'chip')\n"
        "    print('RESULT', json.dumps([key, got]))\n"
    )
    proc = run_hardened(image, project, "python", "-c", script, timeout=600)
    assert proc.returncode == 0, proc.stderr
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            key, got = json.loads(line.removeprefix("RESULT "))
            out[key] = "None" if got is None else got[0] + ": " + got[1]
    assert set(out) == set(cases), proc.stdout
    return out


MOD = (
    "module tt_um_chip(input wire [7:0] ui_in, output reg [7:0] y);\n"
    "  reg [7:0] cnt;\n{}\nendmodule\n"
)

PURE_CASES = {
    "system functions of arguments, parameters and localparams": MOD.format(
        "  localparam [7:0] K = 3;\n  localparam N = 8;\n"
        "  function automatic [7:0] f(input [7:0] a, input signed [3:0] b);\n"
        "    localparam M = 2;\n    reg [7:0] t;\n"
        "    begin\n      t = a ^ K ^ M;\n"
        "      f = $clog2(N) + $signed(b) + $unsigned(a) + $bits(a) + $size(a) + $high(a)\n"
        "          + $low(a) + $clog2(K) + t;\n    end\n  endfunction\n"
        "  always @(*) y = f(ui_in, ui_in[3:0]);"
    ),
    "$bits of a module signal is a constant, not a read": MOD.format(
        "  function automatic [7:0] f(input [7:0] a);\n    f = a + $bits(cnt);\n  endfunction\n"
        "  always @(*) y = f(ui_in);"
    ),
    "a pure function calling another pure function": MOD.format(
        "  function automatic [7:0] g(input [7:0] a);\n    g = a << 1;\n  endfunction\n"
        "  function automatic [7:0] f(input [7:0] a);\n    f = g(a) ^ g(~a);\n  endfunction\n"
        "  always @(*) y = f(ui_in);"
    ),
    "a function in a generate block that uses the genvar": MOD.format(
        "  genvar g;\n  generate for (g = 0; g < 2; g = g + 1) begin : blk\n"
        "    function automatic [7:0] sh(input [7:0] a);\n      sh = a << g;\n"
        "    endfunction\n    wire [7:0] o = sh(ui_in);\n  end endgenerate\n"
        "  always @(*) y = blk[0].o ^ blk[1].o;"
    ),
    "a local array and a loop variable": MOD.format(
        "  function automatic [7:0] f(input [7:0] a);\n    reg [7:0] m [0:3];\n"
        "    integer i;\n    begin\n"
        "      for (i = 0; i < 4; i = i + 1) m[i] = a ^ i[7:0];\n"
        "      f = m[a[1:0]];\n    end\n  endfunction\n"
        "  always @(*) y = f(ui_in);"
    ),
    "a named block with its own local": MOD.format(
        "  function automatic [7:0] f(input [7:0] a);\n    begin : inner\n"
        "      reg [7:0] u;\n      u = a;\n      f = u;\n    end\n  endfunction\n"
        "  always @(*) y = f(ui_in);"
    ),
    "recursion on its own argument": MOD.format(
        "  function automatic [7:0] f(input [7:0] a);\n"
        "    f = (a == 8'd0) ? 8'd0 : a + f(a - 8'd1);\n  endfunction\n"
        "  always @(*) y = f(8'd3) ^ ui_in;"
    ),
    "a parameterized core whose function reads only its arguments": (
        "module tt_um_chip #(parameter W = 8) (input wire [W-1:0] ui_in, "
        "output reg [W-1:0] y);\n"
        "  function automatic [W-1:0] inv(input [W-1:0] a);\n    inv = ~a ^ W;\n"
        "  endfunction\n  always @(*) y = inv(ui_in);\nendmodule\n"
    ),
}

IMPURE_CASES = {
    # pure, but an output argument is refused by the token rules (`equiv_sv`) before the tree is
    # read; here, past them, Icarus cannot compile it (round 13 w01ifctl)
    "a void function that writes its outputs, read after the call": (
        MOD.format(
            "  reg [3:0] h, l;\n"
            "  function automatic void split(input [7:0] v, output [3:0] hi, output [3:0] lo);\n"
            "    hi = v[7:4];\n    lo = v[3:0];\n  endfunction\n"
            "  always @(*) begin\n    split(ui_in, h, l);\n    y = {l, h};\n  end"
        ),
        "port hi is not an input port",
    ),
    # the same as a task (refused by the token rules too): Icarus compiles it, and the
    # read-before-write walk never counts an output argument as written
    "a task that writes its outputs, read after the call": (
        MOD.format(
            "  reg [3:0] h, l;\n"
            "  task automatic split(input [7:0] v, output [3:0] hi, output [3:0] lo);\n"
            "    begin\n      hi = v[7:4];\n      lo = v[3:0];\n    end\n  endtask\n"
            "  always @(*) begin\n    split(ui_in, h, l);\n    y = {l, h};\n  end"
        ),
        "can read `l` before it writes it",
    ),
    "a function that writes a module signal": (
        MOD.format(
            "  function automatic [7:0] f(input [7:0] a);\n    begin\n      cnt = a;\n"
            "      f = a;\n    end\n  endfunction\n  always @(*) y = f(ui_in);"
        ),
        "writes `cnt`",
    ),
    "a call chain to an impure function": (
        MOD.format(
            "  function automatic [7:0] g(input [7:0] a);\n    g = cnt;\n  endfunction\n"
            "  function automatic [7:0] f(input [7:0] a);\n    f = g(a);\n  endfunction\n"
            "  always @(*) y = f(ui_in);"
        ),
        "the function `g`",
    ),
    "a localparam of the same name in a generate block": (
        MOD.format(
            "  function automatic [7:0] f(input [7:0] a);\n    f = a ^ cnt;\n  endfunction\n"
            "  generate if (1) begin : g\n    localparam [7:0] cnt = 8'd0;\n"
            "    wire [7:0] z = cnt;\n  end endgenerate\n"
            "  always @(*) y = f(ui_in) ^ g.z;"
        ),
        "reads `cnt`",
    ),
    "a task": (
        MOD.format(
            "  task automatic t(input [7:0] a);\n    cnt = a;\n  endtask\n"
            "  always @(*) begin\n    t(ui_in);\n    y = cnt;\n  end"
        ),
        "the function `t`",
    ),
    "a hierarchical read": (
        MOD.format(
            "  function automatic [7:0] f(input [7:0] a);\n    f = a ^ tt_um_chip.cnt;\n"
            "  endfunction\n  always @(*) y = f(ui_in);"
        ),
        "uses a hierarchical name",
    ),
    "a function in a generate block that reads a module signal": (
        MOD.format(
            "  genvar g;\n  generate for (g = 0; g < 2; g = g + 1) begin : blk\n"
            "    function automatic [7:0] sh(input [7:0] a);\n      sh = a << g ^ cnt;\n"
            "    endfunction\n    wire [7:0] o = sh(ui_in);\n  end endgenerate\n"
            "  always @(*) y = blk[0].o ^ blk[1].o;"
        ),
        "the function `sh`",
    ),
}


async def test_the_purity_check_accepts_pure_functions_and_refuses_the_rest(
    server: Client, tmp_path: Path, image: str
) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
    cases = dict(PURE_CASES) | {k: v[0] for k, v in IMPURE_CASES.items()}
    got = texts_in_container(image, project, cases)
    for key in PURE_CASES:
        assert got[key] == "None", (key, got[key])
    for key, (_, shown) in IMPURE_CASES.items():
        assert got[key].startswith("guard_sim_synth_differ: ") and shown in got[key], (key, got)


@pytest.mark.parametrize(("design", "fe"), PRECISION_CASES)
async def test_the_purity_check_adds_no_refusal_on_emitted_rtl(
    server: Client, tmp_path: Path, image: str, design: str, fe: str
) -> None:
    async with server as client:
        project = await design_project(client, tmp_path, design, fe)
        emitted = await call(client, "emit_verilog", project=design)
        assert emitted["ok"] is True, emitted["summary"]
        assert purity_in_container(image, project, design) == "None", (design, fe)


@pytest.mark.parametrize("fe", FRONTENDS)
async def test_the_purity_check_adds_no_refusal_in_the_counter(
    server: Client, tmp_path: Path, image: str, fe: str
) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, fe)
        emitted = await call(client, "emit_verilog", project="counter")
        assert emitted["ok"] is True, emitted["summary"]
        assert purity_in_container(image, project, "counter") == "None", fe


async def test_an_untaken_generate_branch_with_impure_blocks_is_not_judged(
    server: Client, tmp_path: Path
) -> None:
    """genpick: an if-generate on an ordinary constant. The branch that is not taken holds an
    impure function and an `always @*` that reads before it writes, but both tools take the other
    branch, so the check passes (an earlier count rule refused this)."""
    async with server as client:
        await design_project(client, tmp_path, "genpick", "verilog")
        result = await call(client, "check_equivalence", project="genpick")
        assert result["ok"] is True, result["summary"]
        assert result["details"]["verdict"] == "equivalent", result["details"]


@pytest.mark.parametrize("design", ["combok", "combone", "memread", "pkgcall", "starglue"])
async def test_clean_blocks_the_elaboration_cross_check_must_not_refuse(
    server: Client, tmp_path: Path, design: str
) -> None:
    """combok/combone: one-assignment `always_comb` blocks (Verilator without -fno-dfg folds them
    away); memread: a register array read outside an always block (Yosys makes it registers);
    pkgcall: `pkg::f(a)` in an assign; starglue: `always@(*)begin`. Both tools elaborate the same
    design, so the check passes."""
    async with server as client:
        await design_project(client, tmp_path, design, "verilog")
        result = await call(client, "check_equivalence", project=design)
        assert result["ok"] is True, result["summary"]
        assert result["details"]["verdict"] == "equivalent", result["details"]


async def test_a_design_with_pure_functions_is_proven_equivalent(
    server: Client, tmp_path: Path
) -> None:
    """fpure: functions that call each other and use a loop, a local and a localparam are not
    refused by the purity check, and the whole proof still runs and passes."""
    async with server as client:
        await design_project(client, tmp_path, "fpure", "verilog")
        result = await call(client, "check_equivalence", project="fpure")
        assert result["ok"] is True, result["summary"]
        assert result["details"]["verdict"] == "equivalent", result["details"]
