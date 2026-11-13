# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every code example in the skills works: it goes through the real MCP tools and the real image.

The examples are the fenced blocks tagged `example=<id>` (see tests/skill_examples.py). Each one
is written into a fresh project, emitted and linted, then run on Icarus and Verilator with the
tests it names (`tests=`); the tests must cover every requirement of the spec. Test-only examples
(a block with only `test/` files) are run through the examples that use them.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp import Client
from skill_examples import Example, parse_examples

from integration.test_emit import call, make_project
from integration.test_sim import outcomes
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

EXAMPLES = parse_examples()
DEFAULT_SPEC = """\
title: Example
summary: A small example design from a skill.
clock_hz: 10000000
reset: "rst_n, active-low, synchronous"
requirements:
  - id: R1
    text: The count goes up by one on every clock while ui_in[0] is 1.
  - id: R2
    text: The count holds its value while ui_in[0] is 0.
  - id: R3
    text: The count wraps from 255 back to 0.
"""


PULSE_SPEC = """\
title: Pulse
summary: Reports busy for four clocks after a start request.
clock_hz: 10000000
reset: "rst_n, active-low, synchronous"
requirements:
  - id: R1
    text: uo_out[0] is 0 while the design is idle after reset.
  - id: R2
    text: A one-clock pulse on ui_in[0] makes uo_out[0] 1 for exactly four clocks.
"""
SHIFTER_SPEC = """\
title: Shifter
summary: An 8-bit shift register that takes ui_in[0] in at bit 0.
clock_hz: 10000000
reset: "rst_n, active-low, synchronous"
requirements:
  - id: R1
    text: On every clock, ui_in[0] becomes bit 0 of uo_out.
  - id: R2
    text: On every clock, the older bits move up one place.
"""
SPECS = {"pulse": PULSE_SPEC, "shifter": SHIFTER_SPEC}


def runnable() -> list[str]:
    """Examples with design files; a test-only example (`fsm-test`) is run through its users."""
    return sorted(
        i for i, ex in EXAMPLES.items() if any(not p.startswith("test/") for p in ex.files)
    )


@pytest.fixture
def server(tmp_path: Path, runner: DockerRunner, image: str) -> Client:
    return Client(build_server(tmp_path, None, runner=runner, image=image))


def test_examples_exist_for_every_front_end_skill() -> None:
    skills = {s for ex in EXAMPLES.values() for s in ex.skills}
    assert {"amaranth-style", "myhdl-style", "cocotb2-tests"} <= skills
    assert {ex.frontend for ex in EXAMPLES.values()} == {"amaranth", "myhdl", "verilog"}
    for ex in EXAMPLES.values():
        if ex.tests_from:
            assert any(p.startswith("test/") for p in EXAMPLES[ex.tests_from].files), ex.id


def test_every_example_is_simulated() -> None:
    """The behaviour the prose claims is checked: no runnable example may lack tests."""
    for example_id in runnable():
        assert test_files(EXAMPLES[example_id]), f"{example_id} has no tests"


def test_files(ex: Example) -> dict[str, str]:
    """The `test/` files of an example, taken from the example named by `tests=` if it has none."""
    own = {p: t for p, t in ex.files.items() if p.startswith("test/")}
    if own or ex.tests_from is None:
        return own
    return test_files(EXAMPLES[ex.tests_from])


test_files.__test__ = False  # type: ignore[attr-defined]


async def write(client: Client, ex: Example, path: str, text: str) -> None:
    if path.startswith("test/"):
        tool = "write_test"
    elif path == "spec.yaml":
        tool = "write_spec"
    else:
        tool = "write_rtl"
    out = (
        await call(client, tool, project=ex.project, path=path, content=text)
        if (tool != "write_spec")
        else await call(client, tool, project=ex.project, content=text)
    )
    assert out["ok"] is True, f"{ex.id}: {path}: {out['summary']}"


@pytest.mark.parametrize("example_id", runnable())
async def test_skill_example(server: Client, tmp_path: Path, example_id: str) -> None:
    ex = EXAMPLES[example_id]
    tests = test_files(ex)
    async with server as client:
        await make_project(client, tmp_path, ex.frontend, ex.project)
        if "spec.yaml" not in ex.files:
            await write(client, ex, "spec.yaml", SPECS.get(ex.project, DEFAULT_SPEC))
        for path, text in {**ex.files, **tests}.items():
            await write(client, ex, path, text)
        emitted = await call(client, "emit_verilog", project=ex.project)
        assert emitted["ok"] is True, f"{ex.id}: {emitted['summary']}"
        assert emitted["details"]["lint"] == [], f"{ex.id}: {emitted['summary']}"
        for sim in ("icarus", "verilator"):
            result = await call(client, "run_tests", project=ex.project, simulator=sim)
            assert result["ok"] is True, f"{ex.id} on {sim}: {result['summary']}"
            assert set(outcomes(result).values()) == {"pass"}, f"{ex.id} on {sim}"
            assert result["details"]["coverage"]["uncovered"] == [], f"{ex.id} on {sim}"


# -- the "common mistakes" the skills describe really are rejected --------------------------------

NO_DOMAIN = """\
from amaranth.hdl import Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        count = Signal(8)
        m.d.sync += count.eq(count + 1)
        m.d.comb += self.uo_out.eq(count)
        return m


def top() -> wiring.Component:
    return Core()
"""
UNUSED_PORT = """\
from myhdl import Signal, always_comb, block


@block
def counter(clk, rst_n, ui_in, uo_out):
    @always_comb
    def drive():
        uo_out.next = ui_in

    return drive
"""


@pytest.mark.parametrize(
    ("frontend", "text", "needle"),
    [
        ("amaranth", NO_DOMAIN, "port 'rst'"),
        ("myhdl", UNUSED_PORT, "ToVerilogWarning"),
    ],
    ids=["amaranth-no-clock-domain", "myhdl-unused-port"],
)
async def test_common_mistake_is_rejected(
    server: Client, tmp_path: Path, frontend: str, text: str, needle: str
) -> None:
    async with server as client:
        await make_project(client, tmp_path, frontend)
        await call(client, "write_rtl", project="counter", path="hdl/counter.py", content=text)
        emitted = await call(client, "emit_verilog", project="counter")
        assert emitted["ok"] is False
        assert needle in emitted["summary"]


@pytest.mark.parametrize("cond", ["left == 1", "left == 0", "left > 1", "~left.any()"])
async def test_amaranth_literal_comparison_rule(server: Client, tmp_path: Path, cond: str) -> None:
    """The width rule in amaranth-style: comparing with a narrow literal is fine in Amaranth."""
    text = (
        EXAMPLES["amaranth-fsm"]
        .files["hdl/pulse.py"]
        .replace("with m.If(left == 0):", f"with m.If({cond}):")
        .replace("pulse", "counter")
    )
    async with server as client:
        await make_project(client, tmp_path, "amaranth")
        await call(client, "write_rtl", project="counter", path="hdl/counter.py", content=text)
        emitted = await call(client, "emit_verilog", project="counter")
        assert emitted["ok"] is True, emitted["summary"]
