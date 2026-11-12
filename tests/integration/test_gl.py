# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Gate-level run_tests against the real toolchain image, through the real MCP tools (M1-2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from mcp import Client

from integration.test_emit import FIXTURES, FRONTENDS, call
from integration.test_ledger import step_states
from integration.test_sim import COUNTER_TESTS, counter_project, outcomes
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

RTL_WAVES = "build/waves/icarus/tb.fst"
GL_WAVES = "build/waves/icarus-gl/tb.fst"


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


@pytest.mark.parametrize("fe", FRONTENDS)
async def test_gl_counter(server: Client, tmp_path: Path, fe: str) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, fe)
        rtl = await call(client, "run_tests", project="counter", simulator="icarus", waves=True)
        assert rtl["ok"] is True, rtl["summary"]
        assert rtl["details"]["waves"] == RTL_WAVES and rtl["details"]["gl"] is None
        rtl_bytes = (project / RTL_WAVES).read_bytes()

        gl = await call(
            client, "run_tests", project="counter", simulator="icarus", gl=True, waves=True
        )
        assert gl["ok"] is True, gl["summary"]
        first = gl["summary"].splitlines()[0]
        assert first.startswith("Gate-level simulation (sky130 netlist, icarus): ok"), first
        assert "verilator" not in gl["summary"].lower()
        assert not any("verilator" in h.lower() for h in gl["next_hints"])
        details = gl["details"]
        assert details["sim"] == "icarus" and details["stage"] == "sim"
        assert set(outcomes(gl)) == COUNTER_TESTS
        assert set(outcomes(gl).values()) == {"pass"}

        # The simulator was given Tiny Tapeout's gate-level defines, minus the power-pin ones.
        defines = details["gl"]["defines"]
        assert defines == {"FUNCTIONAL": "1", "SIM": "1", "UNIT_DELAY": "#1"}
        assert "USE_POWER_PINS" not in defines and "GL_TEST" not in defines
        sha = details["gl"]["netlist_sha256"]
        assert isinstance(sha, str) and len(sha) == 64
        assert details["gl"]["synth"]["cells"] > 8
        assert details["coverage"] is None  # gate-level tests never count for coverage

        # The netlist stays in the run folder (removed with it); build/synth is not written.
        assert not (project / "build" / "synth").exists()
        assert not list((project / "build" / ".ss").glob("*"))

        # The RTL wave file is untouched; the gate-level one is separate and readable.
        assert (project / RTL_WAVES).read_bytes() == rtl_bytes
        assert details["waves"] == GL_WAVES
        assert (project / GL_WAVES).is_file()
        assert (
            hashlib.sha256((project / GL_WAVES).read_bytes()).hexdigest()
            != hashlib.sha256(rtl_bytes).hexdigest()
        )
        rows = await call(
            client, "inspect_waves", project="counter", path=GL_WAVES, signals=["uo_out"]
        )
        # On the gates the output starts unknown (x) until reset has run, then counts.
        values = [row[1] for row in rows["details"]["rows"]]
        assert values[0].endswith("x") and values[1:4] == ["0x00", "0x01", "0x02"], values[:5]
        assert (project / "build" / "sim" / "icarus-gl-waves").is_dir()

        # Status: gate_sim passes, the Icarus row is still the RTL run.
        states = step_states(await call(client, "status", project="counter"))
        assert states["gate_sim"] == "pass"
        assert states["sim_icarus"] == "pass" and states["coverage"] == "not_run"
        assert states["synthesis"] == "not_run"  # run_tests gl is not a synthesize run


async def test_gl_with_verilator_is_an_error(server: Client, tmp_path: Path) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog")
        result = await client.call_tool(
            "run_tests", {"project": "counter", "simulator": "verilator", "gl": True}
        )
        assert result.is_error
        assert "icarus only" in str(result.content)


async def test_gl_mismatch(server: Client, tmp_path: Path) -> None:
    """`initial count = 0;` instead of a reset: RTL passes, the gates start unknown and fail."""
    async with server as client:
        project = await counter_project(
            client, tmp_path, "verilog", design="verilog_initial_value.v"
        )
        wrote = await call(
            client,
            "write_test",
            project="counter",
            path="test/test.py",
            content=(FIXTURES / "test_initial_value.py").read_text(),
        )
        assert wrote["ok"] is True, wrote["summary"]
        rtl = await call(client, "run_tests", project="counter", simulator="icarus", waves=True)
        assert rtl["ok"] is True, rtl["summary"]  # the seeded bug is invisible in RTL
        rtl_bytes = (project / RTL_WAVES).read_bytes()

        gl = await call(client, "run_tests", project="counter", simulator="icarus", gl=True)
        assert gl["ok"] is False, gl["summary"]
        assert outcomes(gl) == {"test_counts_from_power_up": "error"} or outcomes(gl) == {
            "test_counts_from_power_up": "fail"
        }
        lines = gl["summary"].splitlines()
        assert lines[0].startswith("Gate-level simulation (sky130 netlist, icarus): failed")
        first = next(ln for ln in lines if ln.startswith("First difference on the pins"))
        assert first.startswith("First difference on the pins: uo_out at clock cycle ")
        assert "RTL 00000000, gate level xxxxxxxx" in first, first
        assert "have no reset or rely on an initial value" in gl["summary"]
        diff = gl["details"]["gl"]["diff"]
        assert diff["state"] == "differs" and diff["rtl_failed"] is False
        assert diff["signals"][0] == {"signal": "uo_out", "rtl": "00000000", "gl": "xxxxxxxx"}
        assert diff["cycle"] >= 1 and diff["time_ns"] > 0

        # The gate-level waves are kept for the failure and pointed at; the RTL ones are untouched.
        assert gl["details"]["waves"] == GL_WAVES and (project / GL_WAVES).is_file()
        assert any(GL_WAVES in h and "inspect_waves" in h for h in gl["next_hints"])
        assert (project / RTL_WAVES).read_bytes() == rtl_bytes
        # The reference RTL rerun left nothing in build/sim/icarus or build/waves/icarus.
        assert sorted(p.name for p in (project / "build" / "waves").iterdir()) == [
            "icarus",
            "icarus-gl",
        ]
        assert not list((project / "build" / ".ss").glob("*"))

        states = step_states(await call(client, "status", project="counter"))
        assert states["gate_sim"] == "fail"
        assert states["sim_icarus"] == "pass"
