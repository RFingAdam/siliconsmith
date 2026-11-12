# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""emit_verilog against the real toolchain image, through the hardened `docker run`."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT
from mcp import Client

from integration.helpers import run_hardened
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.scaffold import TT_DIR
from siliconsmith.server import build_server
from siliconsmith.worker.wrapper_gen import TT_PORTS
from siliconsmith.wrapper import generate_wrapper

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

FRONTENDS = ("amaranth", "myhdl", "verilog")
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "counter"
GOLDEN = json.loads((REPO_ROOT / "tests" / "golden" / "counter_ports.json").read_text())
ARGS = {"author": "Ada Lovelace", "description": "An 8-bit counter."}
ENTRY = {"amaranth": "hdl/counter.py", "myhdl": "hdl/counter.py", "verilog": "src/counter.v"}
FIXTURE_FILE = {"amaranth": "amaranth.py", "myhdl": "myhdl.py", "verilog": "verilog.v"}


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


async def call(client: Client, tool: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    out: dict[str, Any] = result.structured_content
    assert len(out["summary"].splitlines()) <= 20
    return out


async def make_project(client: Client, root: Path, fe: str, name: str = "counter") -> Path:
    await call(client, "create_project", name=name, frontend=fe, **ARGS)
    return root / name


def put_design(project: Path, fe: str, text: str) -> None:
    (project / ENTRY[fe].replace("counter", project.name)).write_text(text)


async def emit(client: Client, name: str = "counter") -> dict[str, Any]:
    return await call(client, "emit_verilog", project=name)


# -- acceptance: the same counter from all three front-ends -----------------------------------


@pytest.mark.parametrize("fe", FRONTENDS)
async def test_emit_counter(server: Client, tmp_path: Path, image: str, fe: str) -> None:
    async with server as client:
        project = await make_project(client, tmp_path, fe)
        put_design(project, fe, (FIXTURES / FIXTURE_FILE[fe]).read_text())
        first = await emit(client)
        assert first["ok"] is True, first["summary"]
        assert first["details"]["stage"] == "done"
        assert first["details"]["lint"] == []
        assert first["details"]["ports"] == GOLDEN
        assert first["details"]["wrapper_ports"] == {
            p: {"direction": d, "width": w} for p, (d, w) in TT_PORTS.items()
        }
        core = (project / "src" / "counter.v").read_bytes()
        wrapper = (project / "src" / "project.v").read_bytes()
        assert b"module tt_um_counter" in wrapper
        assert b"module counter" in core
        for text in (core, wrapper):
            assert b"/work" not in text and b"/home" not in text
        # The emitted files compile with Icarus (the image's iverilog, under the sandbox flags).
        proc = run_hardened(
            image,
            project,
            "sh",
            "-c",
            "cd /work/src && iverilog -g2012 -o /tmp/counter.vvp project.v counter.v",
        )
        assert proc.returncode == 0, proc.stderr
        # Two emits give byte-identical files.
        second = await emit(client)
        assert second["ok"] is True
        assert (project / "src" / "counter.v").read_bytes() == core
        assert (project / "src" / "project.v").read_bytes() == wrapper
        if fe == "verilog":
            assert core == (FIXTURES / "verilog.v").read_bytes()  # passthrough, untouched
        # The worker's run folders are cleaned up.
        assert not list((project / "build" / ".ss").glob("*"))


# -- acceptance: errors come back with file and line --------------------------------------------

SYNTAX = {
    "amaranth": "from amaranth.hdl import Module\n\ndef top(:\n    pass\n",
    "myhdl": "from myhdl import block\n\n@block\ndef counter(clk:\n    pass\n",
}
ELABORATION = {
    "amaranth": (
        "from amaranth.hdl import Module\n"
        "from amaranth.lib import wiring\n"
        "from amaranth.lib.wiring import In, Out\n\n\n"
        "class Core(wiring.Component):\n"
        "    clk: In(1)\n"
        "    uo_out: Out(8)\n\n"
        "    def elaborate(self, platform):\n"
        "        m = Module()\n"
        "        m.d.comb += self.uo_out.eq(missing_name)\n"
        "        return m\n\n\n"
        "def top():\n"
        "    return Core()\n"
    ),
    "myhdl": (
        "from myhdl import always_comb, block\n\n\n"
        "@block\n"
        "def counter(ui_in, uo_out):\n"
        "    @always_comb\n"
        "    def logic():\n"
        "        uo_out.next = ui_in + missing_name\n\n"
        "    return logic\n"
    ),
}
ELABORATION_LINE = {"amaranth": 12, "myhdl": 8}
SYNTAX_LINE = {"amaranth": 3, "myhdl": 4}


@pytest.mark.parametrize("fe", ["amaranth", "myhdl"])
async def test_emit_errors(server: Client, tmp_path: Path, fe: str) -> None:
    async with server as client:
        project = await make_project(client, tmp_path, fe)
        put_design(project, fe, SYNTAX[fe])
        out = await emit(client)
        assert out["ok"] is False
        error = out["details"]["python_error"]
        assert error["file"] == "hdl/counter.py"
        assert error["line"] == SYNTAX_LINE[fe]
        assert error["kind"] == "SyntaxError"
        assert f"hdl/counter.py:{SYNTAX_LINE[fe]}" in out["summary"]

        put_design(project, fe, ELABORATION[fe])
        out = await emit(client)
        assert out["ok"] is False
        assert out["details"]["stage"] == "elaborate"
        error = out["details"]["python_error"]
        assert error["file"] == "hdl/counter.py"
        assert error["line"] == ELABORATION_LINE[fe]
        assert "missing_name" in error["msg"] or "missing_name" in (error["source"] or "")
        assert f"hdl/counter.py:{ELABORATION_LINE[fe]}" in out["summary"]
        assert "/work" not in out["summary"]


@pytest.mark.parametrize("fe", ["amaranth", "myhdl"])
def test_container_reports_syntax_errors_too(runner: DockerRunner, tmp_path: Path, fe: str) -> None:
    """The host catches syntax errors first; this checks the container's own extractor."""
    project = tmp_path / "counter"
    (project / "hdl").mkdir(parents=True)
    (project / "hdl" / "counter.py").write_text(SYNTAX[fe])
    request = {"name": "counter", "frontend": fe, "author": "Ada"}
    outcome = runner.run("emit", request, project_dir=project, timeout_s=300)
    assert outcome.result is not None, outcome.stderr_tail
    assert outcome.result["stage"] == "elaborate"
    error = outcome.result["python_error"]
    assert (error["kind"], error["file"], error["line"]) == (
        "SyntaxError",
        "hdl/counter.py",
        SYNTAX_LINE[fe],
    )


# -- acceptance: lint findings are structured ---------------------------------------------------

WIDTH_BUG = """\
`default_nettype none

module counter (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);
  wire [8:0] wide = {1'b0, ui_in} + 9'd1;
  assign uo_out = wide;  // 9 bits into 8: Verilator reports WIDTHTRUNC
endmodule
"""


async def test_lint_width(server: Client, tmp_path: Path) -> None:
    async with server as client:
        project = await make_project(client, tmp_path, "verilog")
        put_design(project, "verilog", WIDTH_BUG)
        out = await emit(client)
    assert out["ok"] is False
    assert out["details"]["stage"] == "lint"
    findings = out["details"]["lint"]
    width = [f for f in findings if f["code"] in ("WIDTHTRUNC", "WIDTHEXPAND")]
    assert len(width) == 1, findings
    finding = width[0]
    assert finding["code"] == "WIDTHTRUNC"
    assert finding["severity"] == "warning"
    assert finding["file"] == "src/counter.v"
    assert (finding["line"], finding["col"]) == (8, 17)
    assert "expects 8 bits" in finding["msg"]
    assert "src/counter.v:8:17" in out["summary"]
    # A lint failure still leaves the wrapper in place, and the hand-written core untouched.
    assert (project / "src" / "project.v").is_file()
    assert (project / "src" / "counter.v").read_text() == WIDTH_BUG


async def test_ports_that_do_not_fit_are_a_design_error(server: Client, tmp_path: Path) -> None:
    async with server as client:
        project = await make_project(client, tmp_path, "verilog")
        put_design(
            project,
            "verilog",
            "module counter (input wire [3:0] ui_in, output wire [7:0] uo_out);\n"
            "  assign uo_out = {4'b0, ui_in};\nendmodule\n",
        )
        out = await emit(client)
    assert out["ok"] is False
    assert out["details"]["stage"] == "ports"
    assert "ui_in" in out["summary"] and "8 bit" in out["summary"]


async def test_amaranth_without_a_sync_domain_gets_a_hint(server: Client, tmp_path: Path) -> None:
    source = (
        "from amaranth.hdl import Module, Signal\n"
        "from amaranth.lib import wiring\n"
        "from amaranth.lib.wiring import In, Out\n\n\n"
        "class Core(wiring.Component):\n"
        "    ui_in: In(8)\n"
        "    uo_out: Out(8)\n\n"
        "    def elaborate(self, platform):\n"
        "        m = Module()\n"
        "        count = Signal(8)\n"
        "        m.d.sync += count.eq(count + self.ui_in)\n"
        "        m.d.comb += self.uo_out.eq(count)\n"
        "        return m\n\n\n"
        "def top():\n"
        "    return Core()\n"
    )
    async with server as client:
        project = await make_project(client, tmp_path, "amaranth")
        put_design(project, "amaranth", source)
        out = await emit(client)
    assert out["ok"] is False
    assert out["details"]["stage"] == "ports"
    assert "ClockDomain('sync')" in out["details"]["message"]


# -- from M0-3: the scaffold's wrapper ports, read back with Yosys -------------------------------


def yosys_ports(image: str, project: Path, mount: Path, command: str, top: str) -> dict[str, Any]:
    script = f"read_verilog -sv {command}; hierarchy -top {top}; write_json /work/build/{top}.json"
    proc = run_hardened(image, project, "yosys", "-q", "-p", script, mount=mount)
    assert proc.returncode == 0, proc.stderr
    modules = json.loads((project / "build" / f"{top}.json").read_text())["modules"]
    return {
        name: (port["direction"], len(port["bits"])) for name, port in modules[top]["ports"].items()
    }


@pytest.mark.parametrize("fe", FRONTENDS)
async def test_scaffold_ports_yosys(server: Client, tmp_path: Path, image: str, fe: str) -> None:
    async with server as client:
        project = await make_project(client, tmp_path, fe, name="chip")
        # The untouched starter emits and lints clean (this covers the MyHDL starter too).
        out = await emit(client, "chip")
    assert out["ok"] is True, out["summary"]
    ours = yosys_ports(image, project, TT_DIR, "/work/src/project.v /work/src/chip.v", "tt_um_chip")
    reference = yosys_ports(image, project, TT_DIR, "/smoke/src/project.v", "tt_um_example")
    assert ours == reference
    assert ours == dict(TT_PORTS)


# -- the host and the container make the same wrapper -------------------------------------------


def test_container_and_host_wrappers_are_identical(runner: DockerRunner, tmp_path: Path) -> None:
    project = tmp_path / "counter"
    (project / "src").mkdir(parents=True)
    shutil.copy(FIXTURES / "verilog.v", project / "src" / "counter.v")
    request = {"name": "counter", "frontend": "verilog", "author": "Ada Lovelace"}
    outcome = runner.run("emit", request, project_dir=project, timeout_s=300)
    assert outcome.result is not None and outcome.result["ok"] is True, outcome.stderr_tail
    out_dir = project / "build" / ".ss" / outcome.result["run_id"] / "out"
    ports = {p: d["direction"] for p, d in outcome.result["ports"].items()}
    assert (out_dir / "project.v").read_text() == generate_wrapper("counter", ports, "Ada Lovelace")
