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
    # a constant that is x to Yosys and a simulator (`1/0`, `0 ** -1`) is 0 to Verilator,
    # which then drops the generate branch (or module) that Yosys keeps
    ("divgen", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("divmod", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("powgen", "verilog", "not_proven", "guard_sim_synth_differ"),
    ("divcomb", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Icarus ends a `//` comment at a lone CR, Yosys and Verilator do not
    ("crhide", "verilog", "not_proven", "guard_sim_synth_differ"),
    # Verilator ends an attribute at a `*)` inside its string, Yosys does not
    ("attrhide", "verilog", "not_proven", "guard_sim_synth_differ"),
    # `unique case`: synthesis assumes what a simulator does not
    ("uniquecase", "verilog", "not_proven", "guard_sim_synth_differ"),
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


async def test_a_read_and_a_write_across_loop_iterations_in_verilog_is_not_detected(
    server: Client, tmp_path: Path
) -> None:
    """vloopit, PINNED AS NOT DETECTED: hand-written `always @*` whose loop reads `t` in iteration
    0 and writes it in iteration 1. A simulator keeps the old `t`, the gates use the new one, so
    the two differ; but Verilator's ALWCOMBORDER cannot see an order that only exists across loop
    iterations, and the check has no other way to see it. It is listed under what the check does
    not detect (gate-level simulation is the check for that). If this test starts to fail
    because the check now refuses the design, update the docs and this test."""
    async with server as client:
        await design_project(client, tmp_path, "vloopit", "verilog")
        result = await call(client, "check_equivalence", project="vloopit")
        assert result["details"]["verdict"] == "equivalent", result["summary"]


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


def purity_in_container(image: str, project: Path, name: str) -> str:
    """What `equiv._function_purity` says about the project's emitted RTL (`src/`), run in the
    container where Verilator is: "None" when every function is pure."""
    script = (
        "from pathlib import Path\n"
        "from siliconsmith.worker import equiv\n"
        f"name = {name!r}\n"
        "src = Path('/work/src')\n"
        "files = ('project.v', name + '.v')\n"
        "sources = [(f, (src / f).read_bytes().decode('latin-1')) for f in files]\n"
        "print('RESULT', equiv._function_purity(Path('/work/build/pu'), sources, name))\n"
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
    """`equiv._function_purity` on each text (a whole `tt_um_chip` module, in `chip.v`)."""
    script = (
        "import json\nfrom pathlib import Path\n"
        "from siliconsmith.worker import equiv\n"
        f"cases = json.loads({json.dumps(cases)!r})\n"
        "for i, (key, text) in enumerate(cases.items()):\n"
        "    sources = [('project.v', '// wrapper\\n'), ('chip.v', text)]\n"
        "    got = equiv._function_purity(Path(f'/work/build/pu{i}'), sources, 'chip')\n"
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
    "a void function that only writes its own outputs": MOD.format(
        "  reg [3:0] h, l;\n"
        "  function automatic void split(input [7:0] v, output [3:0] hi, output [3:0] lo);\n"
        "    hi = v[7:4];\n    lo = v[3:0];\n  endfunction\n"
        "  always @(*) begin\n    split(ui_in, h, l);\n    y = {l, h};\n  end"
    ),
    "recursion on its own argument": MOD.format(
        "  function automatic [7:0] f(input [7:0] a);\n"
        "    f = (a == 8'd0) ? 8'd0 : a + f(a - 8'd1);\n  endfunction\n"
        "  always @(*) y = f(8'd3);"
    ),
    "a parameterized core whose function reads only its arguments": (
        "module tt_um_chip #(parameter W = 8) (input wire [W-1:0] ui_in, "
        "output reg [W-1:0] y);\n"
        "  function automatic [W-1:0] inv(input [W-1:0] a);\n    inv = ~a ^ W;\n"
        "  endfunction\n  always @(*) y = inv(ui_in);\nendmodule\n"
    ),
}

IMPURE_CASES = {
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
