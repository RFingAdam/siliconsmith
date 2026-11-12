# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

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
    clock_from_inverter,
    combinational_loop,
    hold_flop_on_tied_output,
    hold_flop_with_init_attribute,
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
    source = (DESIGNS / name / SOURCE_FILE[fe]).read_text()
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
