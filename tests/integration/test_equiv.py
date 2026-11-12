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
from netlist_edit import swap_cell_type

from integration.helpers import run_hardened
from integration.test_emit import FRONTENDS, call
from integration.test_ledger import step_states
from integration.test_sim import counter_project
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

EQUIV_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "equiv"


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
