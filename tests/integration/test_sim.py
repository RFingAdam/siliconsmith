# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""run_tests and inspect_waves against the real toolchain image, through the real MCP tools."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from integration.helpers import hardened
from integration.test_emit import (
    ENTRY,
    FIXTURE_FILE,
    FIXTURES,
    FRONTENDS,
    call,
    make_project,
    put_design,
)
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server
from siliconsmith.worker import results as worker_results

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SIMULATORS = ("icarus", "verilator")
CELLS = [(fe, sim) for fe in FRONTENDS for sim in SIMULATORS]
CELL_IDS = [f"{fe}-{sim}" for fe, sim in CELLS]
WRAP_ASSERT = "assert await run_enabled(dut, 255) == 255"
COUNTER_TESTS = {
    "test_reset_clears_count",
    "test_counts_when_enabled",
    "test_holds_when_disabled",
    "test_wraps_at_255",
}


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


async def counter_project(
    client: Client, root: Path, fe: str, *, design: str | None = None
) -> Path:
    """A counter project with the fixture spec and tests, written through the MCP tools."""
    project = await make_project(client, root, fe)
    if design is None:
        put_design(project, fe, (FIXTURES / FIXTURE_FILE[fe]).read_text())
    else:
        put_design(project, fe, (FIXTURES / design).read_text())
    spec = await call(
        client, "write_spec", project="counter", content=(FIXTURES / "spec.yaml").read_text()
    )
    assert spec["ok"] is True, spec["summary"]
    wrote = await call(
        client,
        "write_test",
        project="counter",
        path="test/test.py",
        content=(FIXTURES / "test.py").read_text(),
    )
    assert wrote["ok"] is True, wrote["summary"]
    return project


def outcomes(result: dict[str, Any]) -> dict[str, str]:
    return {t["name"]: t["outcome"] for t in result["details"]["tests"]}


# -- acceptance: the counter passes on both simulators, for all three front-ends -----------------


@pytest.mark.parametrize(("fe", "sim"), CELLS, ids=CELL_IDS)
async def test_counter_sim(server: Client, tmp_path: Path, fe: str, sim: str) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, fe)
        result = await call(client, "run_tests", project="counter", simulator=sim)
        assert result["ok"] is True, result["summary"]
        details = result["details"]
        assert details["sim"] == sim
        assert details["stage"] == "sim"
        assert details["filtered"] is False
        assert set(outcomes(result).values()) == {"pass"}
        assert set(outcomes(result)) == COUNTER_TESTS
        by_name = {t["name"]: t for t in details["tests"]}
        assert by_name["test_counts_when_enabled"]["covers"] == ["R1"]
        assert by_name["test_wraps_at_255"]["covers"] == ["R3"]
        assert details["coverage"] == {
            "covered": ["R1", "R2", "R3"],
            "uncovered": [],
            "unknown": [],
        }
        assert details["waves"] is None
        assert not (project / "build" / "waves").exists()  # no wave file unless asked for
        # run_tests regenerated the wrapper (and the core) exactly like emit_verilog does.
        assert b"module tt_um_counter" in (project / "src" / "project.v").read_bytes()
        assert not list((project / "build" / ".ss").glob("*"))
        # A second run reuses the build and agrees.
        again = await call(client, "run_tests", project="counter", simulator=sim)
        assert outcomes(again) == outcomes(result)


# -- acceptance: a seeded off-by-one gives an actionable failure ---------------------------------


@pytest.mark.parametrize("sim", SIMULATORS)
async def test_seeded_wrap_bug(server: Client, tmp_path: Path, sim: str) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog", design="verilog_wrap_bug.v")
        result = await call(client, "run_tests", project="counter", simulator=sim)
        assert result["ok"] is False
        summary = result["summary"]
        assert len(summary.splitlines()) <= 20 and len(summary) <= 2000
        failing = [t for t in result["details"]["tests"] if t["outcome"] != "pass"]
        assert [t["name"] for t in failing] == ["test_wraps_at_255"]
        bad = failing[0]
        line = next(
            n
            for n, text in enumerate((FIXTURES / "test.py").read_text().splitlines(), 1)
            if text.strip() == WRAP_ASSERT
        )
        assert (bad["file"], bad["line"]) == ("test/test.py", line)
        assert bad["assertion"] == WRAP_ASSERT
        assert (bad["expected"], bad["actual"]) == ("255", "0")
        # The summary names the test, where, the assertion, and expected vs actual.
        for needed in (
            "test_wraps_at_255",
            f"test/test.py:{line}",
            WRAP_ASSERT,
            "expected: 255",
            "actual:   0",
        ):
            assert needed in summary, summary
        assert "R3" in result["details"]["coverage"]["uncovered"]
        assert any("waves=True" in hint for hint in result["next_hints"])


# -- acceptance: wave tables stay small unless more rows are asked for ---------------------------


@pytest.mark.parametrize("sim", SIMULATORS)
async def test_inspect_waves_rows(server: Client, tmp_path: Path, sim: str) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog")
        ran = await call(client, "run_tests", project="counter", simulator=sim, waves=True)
        assert ran["ok"] is True, ran["summary"]
        path = ran["details"]["waves"]
        assert path in {"build/waves/icarus/tb.fst", "build/waves/verilator/tb.fst"}
        assert path.split("/")[2] == sim
        assert [a["path"] for a in ran["artifacts"] if a["kind"] == "waves"] == [path]
        default = await call(
            client, "inspect_waves", project="counter", path=path, signals=["uo_out"]
        )
        details = default["details"]
        assert details["columns"][0]["name"] == "tb.uo_out"
        assert len(details["rows"]) <= 50 and details["truncated"] is True
        assert details["rows_total"] > 50
        assert "more rows not shown" in default["summary"]
        # The count goes 0, 1, 2, ... as hex rows.
        assert [row[1] for row in details["rows"][:4]] == ["0x00", "0x01", "0x02", "0x03"]
        more = await call(
            client, "inspect_waves", project="counter", path=path, signals=["uo_out"], max_rows=1000
        )
        assert len(more["details"]["rows"]) == more["details"]["rows_total"] > 50
        assert more["details"]["truncated"] is False
        assert more["details"]["rows"][-2][1] == "0xff" and more["details"]["rows"][-1][1] == "0x00"
        # A time window and a second signal (a bit and a bus), by hierarchical suffix.
        window = await call(
            client,
            "inspect_waves",
            project="counter",
            path=path,
            signals=["clk", "user_project.core.count"],
            start_ns=200,
            end_ns=230,
        )
        rows = window["details"]["rows"]
        assert rows and all(200 <= float(r[0]) <= 230 for r in rows)
        assert {c["name"] for c in window["details"]["columns"]} == {
            "tb.clk",
            "tb.user_project.core.count",
        }
        assert set(rows[0][1]) <= {"0", "1"}
        for bad in ("uo_ot", "nothing_like_it"):
            result = await client.call_tool(
                "inspect_waves", {"project": "counter", "path": path, "signals": [bad]}
            )
            assert result.is_error
            assert bad in str(result.content)
        escape = await client.call_tool(
            "inspect_waves",
            {"project": "counter", "path": "build/waves/../../spec.yaml", "signals": ["uo_out"]},
        )
        assert escape.is_error


async def test_inspect_waves_needs_a_run(server: Client, tmp_path: Path) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog")
        result = await client.call_tool(
            "inspect_waves",
            {"project": "counter", "path": "build/waves/icarus/tb.fst", "signals": ["uo_out"]},
        )
        assert result.is_error and "run_tests" in str(result.content)


# -- filter, stub project, timeout ---------------------------------------------------------------


async def test_filtered_run(server: Client, tmp_path: Path) -> None:
    async with server as client:
        await counter_project(client, tmp_path, "verilog")
        result = await call(
            client, "run_tests", project="counter", simulator="icarus", test_filter="wraps"
        )
        assert result["ok"] is True
        assert outcomes(result) == {"test_wraps_at_255": "pass"}
        assert result["details"]["filtered"] is True
        none = await call(
            client, "run_tests", project="counter", simulator="icarus", test_filter="nonesuch"
        )
        assert none["ok"] is False and "matched no test" in none["summary"]


@pytest.mark.parametrize(("fe", "sim"), CELLS, ids=CELL_IDS)
async def test_stub_project_passes(server: Client, tmp_path: Path, fe: str, sim: str) -> None:
    """The project that create_project writes passes its own starter test, untouched."""
    async with server as client:
        await make_project(client, tmp_path, fe)
        result = await call(client, "run_tests", project="counter", simulator=sim)
        assert result["ok"] is True, result["summary"]
        assert outcomes(result) == {"test_adds_inputs": "pass"}


async def test_emit_failure_means_no_simulation(server: Client, tmp_path: Path) -> None:
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        text = (project / ENTRY["verilog"]).read_text().replace("count <= 8'd0", "count <= 8'd0 +")
        (project / ENTRY["verilog"]).write_text(text)
        result = await call(client, "run_tests", project="counter", simulator="icarus")
        assert result["ok"] is False
        assert result["details"]["tests"] == []
        assert result["details"]["stage"] in ("lint", "ports")
        assert result["summary"].startswith("run_tests did not simulate")


HANG = '''\
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import Event


@cocotb.test()
async def test_waits_forever(dut):
    """Covers: R1"""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await Event().wait()
'''


async def test_hanging_test_times_out(
    server: Client, tmp_path: Path, runner: DockerRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("siliconsmith.tools.run_tests.SIM_TIMEOUT_S", 40.0)
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog")
        (project / "test" / "test.py").write_text(HANG)
        result = await call(client, "run_tests", project="counter", simulator="icarus")
        assert result["ok"] is False
        assert (
            "simulation did not finish in 40 s (a test may be waiting forever)" in result["summary"]
        )
        assert result["details"]["timed_out"] is True
        name = runner.last_container
        assert name is not None
        listing = subprocess.run(
            ["docker", "ps", "-a", "-q", "--filter", f"name=^/{name}$"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert listing.stdout.strip() == ""  # the container is gone
        assert not list((project / "build" / ".ss").glob("*"))


# -- parity with Tiny Tapeout's own test/Makefile flow -------------------------------------------


@pytest.mark.parametrize("design", ["verilog.v", "verilog_wrap_bug.v"], ids=["clean", "seeded-bug"])
async def test_tt_makefile_parity(server: Client, tmp_path: Path, image: str, design: str) -> None:
    """TT's `make` in test/ (SIM=icarus) reports the same pass/fail per test as run_tests."""
    async with server as client:
        project = await counter_project(client, tmp_path, "verilog", design=design)
        ours = await call(client, "run_tests", project="counter", simulator="icarus")
    copy = tmp_path / "parity_copy"
    shutil.copytree(project, copy, ignore=shutil.ignore_patterns("build", ".siliconsmith"))
    command = ["sh", "-c", "cd /parity/test && make SIM=icarus"]
    argv = hardened(image, copy, *command)
    at = len(argv) - len(command) - 1
    assert argv[at] == image
    argv[at:at] = ["--mount", f"type=bind,src={copy},dst=/parity"]  # the one writable mount
    proc = subprocess.run(
        argv, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300, check=False
    )
    assert (copy / "test" / "results.xml").is_file(), proc.stdout[-2000:] + proc.stderr[-2000:]
    theirs = {
        r["name"]: r["outcome"]
        for r in worker_results.parse_junit((copy / "test" / "results.xml").read_bytes())
    }
    assert theirs == outcomes(ours)
    assert ("fail" in theirs.values()) == (not ours["ok"])
