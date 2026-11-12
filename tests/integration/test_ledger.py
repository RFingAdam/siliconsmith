# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The validation ledger, status and the held-out reference test, against the real toolchain."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT
from mcp import Client

from integration.test_emit import call
from integration.test_sim import CELL_IDS, CELLS, SIMULATORS, counter_project
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

REFERENCE = REPO_ROOT / "tests" / "reference" / "test_counter_reference.py"


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


async def use_reference_test(client: Client) -> None:
    wrote = await call(
        client,
        "write_test",
        project="counter",
        path="test/test.py",
        content=REFERENCE.read_text(),
    )
    assert wrote["ok"] is True, wrote["summary"]


def step_states(status: dict[str, Any]) -> dict[str, str]:
    return {s["key"]: s["state"] for s in status["details"]["steps"]}


# -- acceptance: the frozen reference test passes the counter and fails the seeded bug -----------


@pytest.mark.parametrize(("fe", "sim"), CELLS, ids=CELL_IDS)
async def test_reference_test_passes_the_counter(
    server: Client, tmp_path: Path, fe: str, sim: str
) -> None:
    async with server as client:
        await counter_project(client, tmp_path, fe)
        await use_reference_test(client)
        result = await call(client, "run_tests", project="counter", simulator=sim)
        assert result["ok"] is True, result["summary"]
        assert [t["name"] for t in result["details"]["tests"]] == ["test_counter_reference"]
        assert result["details"]["tests"][0]["outcome"] == "pass"


@pytest.mark.parametrize("sim", SIMULATORS)
async def test_reference_test_fails_the_wrap_bug(server: Client, tmp_path: Path, sim: str) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog", design="verilog_wrap_bug.v")
        await use_reference_test(client)
        result = await call(client, "run_tests", project="counter", simulator=sim)
        assert result["ok"] is False
        assert result["details"]["tests"][0]["outcome"] == "fail"
        assert "expected" in result["summary"]


# -- acceptance: full runs on both simulators set the steps; a filtered run changes nothing ------


async def test_ledger_follows_full_runs_only(server: Client, tmp_path: Path) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        before = step_states(await call(client, "status", project="counter"))
        assert before["spec_valid"] == "pass"
        for key in ("check", "sim_icarus", "sim_verilator", "coverage", "spec_approved"):
            assert before[key] == "not_run"

        emitted = await call(client, "emit_verilog", project="counter")
        assert emitted["ok"] is True, emitted["summary"]
        assert step_states(await call(client, "status", project="counter"))["check"] == "pass"

        first = await call(client, "run_tests", project="counter", simulator="icarus")
        assert first["ok"] is True, first["summary"]
        mid = step_states(await call(client, "status", project="counter"))
        assert (mid["sim_icarus"], mid["sim_verilator"], mid["coverage"]) == (
            "pass",
            "not_run",
            "not_run",
        )

        second = await call(client, "run_tests", project="counter", simulator="verilator")
        assert second["ok"] is True, second["summary"]
        full = await call(client, "status", project="counter")
        states = step_states(full)
        assert (states["sim_icarus"], states["sim_verilator"], states["coverage"]) == (
            "pass",
            "pass",
            "pass",
        )
        assert states["spec_approved"] == "not_run"  # only the owner can approve
        assert states["synthesis"] == "not_run"  # nothing has called synthesize yet
        later = ("gate_sim", "equivalence", "layout", "tt_precheck", "bring_up")
        assert [states[k] for k in later] == ["not_available"] * 5

        # A filtered run, even a failing one, changes nothing.
        filtered = await call(
            client, "run_tests", project="counter", simulator="icarus", test_filter="wraps"
        )
        assert filtered["details"]["filtered"] is True
        again = await call(client, "status", project="counter")
        assert step_states(again) == states
        # The simulation and coverage rows read exactly as before (the check row may name the
        # newer run, which did re-check the Verilog).
        rows = {s["key"]: s for s in full["details"]["steps"]}
        for step in again["details"]["steps"]:
            if step["key"] != "check":
                assert step == rows[step["key"]]

        # Changing a test makes both simulations and coverage stale, and not the check.
        test_file = project / "test" / "test.py"
        edited = test_file.read_text() + "\n# a comment\n"
        wrote = await call(
            client, "write_test", project="counter", path="test/test.py", content=edited
        )
        assert wrote["ok"] is True
        stale = step_states(await call(client, "status", project="counter"))
        assert stale["check"] == "pass"
        assert (stale["sim_icarus"], stale["sim_verilator"], stale["coverage"]) == ("stale",) * 3

        doc = (project / "docs" / "verification.md").read_text()
        assert "| Step | What it proves | Status | Evidence | Date |" in doc
