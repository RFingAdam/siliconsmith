# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""synthesize against the real toolchain image, through the real MCP tools (PLAN.md M1-1)."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from mcp import Client

from integration.test_emit import ENTRY, FRONTENDS, call, make_project
from integration.test_ledger import step_states
from integration.test_sim import counter_project
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SYNTH = REPO_ROOT / "tests" / "fixtures" / "synth"
LISTS = "/opt/pdk/sky130A/libs.tech/openlane/sky130_fd_sc_hd"
CELL_INSTANCE = re.compile(r"^\s*(sky130_fd_sc_hd__\w+)\s+\S+\s*\(", re.M)


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


def do_not_use_cells(image: str) -> set[str]:
    """Every cell of the image's no_synth.cells and drc_exclude.cells."""
    cells: set[str] = set()
    for name in ("no_synth.cells", "drc_exclude.cells"):
        proc = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "cat", image,
             f"{LISTS}/{name}"],
            capture_output=True,
            text=True,
            check=False,
        )  # fmt: skip
        assert proc.returncode == 0, proc.stderr
        cells |= {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
    assert len(cells) > 200  # the lists really were read
    return cells


def put_core(project: Path, fe: str, text: str) -> None:
    (project / ENTRY[fe]).write_text(text)


# -- acceptance: the counter, on all three front-ends --------------------------------------------


@pytest.mark.parametrize("fe", FRONTENDS)
async def test_synth_counter(server: Client, tmp_path: Path, image: str, fe: str) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, fe)
        result = await call(client, "synthesize", project="counter")
        assert result["ok"] is True, result["summary"]
        details = result["details"]
        assert details["stage"] == "synth" and details["frontend"] == fe
        # Cells: 8 flip-flops hold the count, the rest is logic (plus the constant-output ties).
        assert details["cells"] > 8
        assert details["flip_flops"] == 8
        assert details["combinational"] == details["cells"] - 8
        assert 100 < details["area_um2"] < 2000
        top = details["top_cells"]
        assert 1 <= len(top) <= 10
        assert [c["count"] for c in top] == sorted((c["count"] for c in top), reverse=True)
        assert all(c["cell"].startswith("sky130_fd_sc_hd__") for c in top)
        assert sum(c["count"] for c in top) <= details["cells"]
        util = details["utilization"]
        assert util["tiles"] == "1x1" and util["verdict"] == "ok"
        assert 0 < util["estimate"] < 0.2
        assert "estimate" in util["note"] and "layout" in util["note"]
        assert details["latches"] == [] and details["checks"] == [] and details["warnings"] == []
        assert details["clock_period_ns"] == 100  # 10 MHz, from spec.yaml
        assert details["yosys_seconds"] < 120
        first = result["summary"].splitlines()[0]
        assert first.startswith("Synthesis passed: ") and "estimate" in first

        # The netlist is in the tool-owned folder, and the hash is of its bytes.
        netlist = project / details["netlist"]
        assert details["netlist"] == "build/synth/tt_um_counter.v"
        data = netlist.read_bytes()
        assert details["netlist_sha256"] == hashlib.sha256(data).hexdigest()
        assert b"module tt_um_counter" in data
        assert (project / "build" / "synth" / "stat.json").is_file()
        assert not list((project / "build" / ".ss").glob("*"))

        # The flow's do-not-use lists are honoured: no listed cell and no lpflow_ cell.
        text = data.decode()
        used = set(CELL_INSTANCE.findall(text))
        assert any("dfxtp" in c for c in used)
        assert not used & do_not_use_cells(image), used & do_not_use_cells(image)
        assert "lpflow_" not in text
        # The config files follow the spec.
        assert '"CLOCK_PERIOD": 100' in (project / "src" / "config.json").read_text()

        states = step_states(await call(client, "status", project="counter"))
        assert states["synthesis"] == "pass"
        assert states["check"] == "pass"  # synthesize emitted and linted the design as well


async def test_synth_counter_is_the_same_for_the_hand_written_core_twice(
    server: Client, tmp_path: Path
) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog")
        first = await call(client, "synthesize", project="counter")
        second = await call(client, "synthesize", project="counter")
        assert first["details"]["netlist_sha256"] == second["details"]["netlist_sha256"]


# -- status: only the synthesis inputs make Synthesis stale ---------------------------------------


async def test_clock_change_makes_synthesis_stale_but_not_check_or_simulation(
    server: Client, tmp_path: Path
) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        simulated = await call(client, "run_tests", project="counter", simulator="icarus")
        assert simulated["ok"] is True, simulated["summary"]
        synthesized = await call(client, "synthesize", project="counter")
        assert synthesized["ok"] is True, synthesized["summary"]
        before = step_states(await call(client, "status", project="counter"))
        assert (before["check"], before["sim_icarus"], before["synthesis"]) == ("pass",) * 3

        spec = (project / "spec.yaml").read_text()
        assert "clock_hz: 10000000" in spec
        wrote = await call(
            client,
            "write_spec",
            project="counter",
            content=spec.replace("clock_hz: 10000000", "clock_hz: 20000000"),
        )
        assert wrote["ok"] is True, wrote["summary"]
        assert '"CLOCK_PERIOD": 50' in (project / "src" / "config.json").read_text()
        after = step_states(await call(client, "status", project="counter"))
        assert after["synthesis"] == "stale"
        assert (after["check"], after["sim_icarus"]) == ("pass", "pass")

        again = await call(client, "synthesize", project="counter")
        assert again["ok"] is True and again["details"]["clock_period_ns"] == 50
        assert step_states(await call(client, "status", project="counter"))["synthesis"] == "pass"


# -- latches -------------------------------------------------------------------------------------


def line_of(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), start=1) if needle in ln)


@pytest.mark.parametrize("fe", ["verilog", "myhdl"])
async def test_synth_latch(server: Client, tmp_path: Path, fe: str) -> None:
    """An accidental latch stops synthesize at the lint step, with file and line."""
    async with server as client:
        project = await make_project(client, tmp_path, fe)
        source = (SYNTH / ("latch_comb.v" if fe == "verilog" else "myhdl_latch.py")).read_text()
        put_core(project, fe, source)
        result = await call(client, "synthesize", project="counter")
        assert result["ok"] is False
        assert result["details"]["stage"] == "lint"
        assert result["summary"].startswith("synthesize stopped before synthesis")
        (finding,) = result["details"]["lint"]
        assert finding["code"] == "LATCH" and finding["file"] == "src/counter.v"
        assert f"src/counter.v:{finding['line']}" in result["summary"]
        assert not (project / "build" / "synth").exists()
        if fe == "verilog":
            assert finding["line"] == line_of(source, "always @*")
            return
        # MyHDL: the generated line and the hdl/ function and line.
        generated = (project / "src" / "counter.v").read_text().splitlines()
        assert generated[finding["line"] - 1].lstrip().startswith("always @")
        assert finding["hdl_file"] == "hdl/counter.py"
        assert finding["hdl_function"] == "remember"
        assert finding["hdl_line"] == line_of(source, "held.next = count")
        assert f"hdl/counter.py:{finding['hdl_line']}, function remember" in result["summary"]
        assert any("hdl/counter.py" in h for h in result["next_hints"])


async def test_synth_explicit_latch_maps_to_a_latch_cell_and_warns(
    server: Client, tmp_path: Path
) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        source = (SYNTH / "latch_explicit.v").read_text()
        put_core(project, "verilog", source)
        result = await call(client, "synthesize", project="counter")
        assert result["ok"] is True, result["summary"]
        details = result["details"]
        (latch,) = details["latches"]
        assert latch["signal"] == "held" and latch["file"] == "src/counter.v"
        assert latch["line"] == line_of(source, "always_latch begin")
        (warning,) = details["warnings"]
        assert f"src/counter.v:{latch['line']}" in warning
        assert any("Warning: Latch" in ln for ln in result["summary"].splitlines())
        netlist = (project / details["netlist"]).read_text()
        assert "sky130_fd_sc_hd__dlxtp" in netlist  # the latch_map.v cell
        assert details["flip_flops"] == 8  # the latches are not counted as flip-flops


# -- too big -------------------------------------------------------------------------------------


async def test_synth_oversize_design_stops_before_abc(server: Client, tmp_path: Path) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        first = await call(client, "synthesize", project="counter")
        assert first["ok"] is True
        assert (project / "build" / "synth" / "tt_um_counter.v").exists()
        put_core(project, "verilog", (SYNTH / "oversize.v").read_text())
        started = time.monotonic()
        result = await call(client, "synthesize", project="counter")
        elapsed = time.monotonic() - started
        assert result["ok"] is False
        assert elapsed < 90, elapsed  # the full flow for this design takes about two minutes
        lines = result["summary"].splitlines()
        assert lines[0].startswith("Too big for 1x1: synthesis was stopped early")
        details = result["details"]
        assert details["stage"] == "too_big"
        assert details["estimate"]["generic_area_um2"] > 10 * 9896
        assert details["utilization"]["verdict"] == "too_big"
        assert any("bigger tile" in h and "owner" in h for h in result["next_hints"])
        assert any("smaller" in h for h in result["next_hints"])
        # no netlist: abc never ran, and the earlier counter netlist is gone, not left behind
        assert not (project / "build" / "synth").exists()
        states = step_states(await call(client, "status", project="counter"))
        assert states["synthesis"] == "fail"
