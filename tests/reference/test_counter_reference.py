# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Frozen reference test for the demo counter (held out from the agents).

The agent that designs the counter writes its own tests from the prompt. This file is a second,
independent check that the finished design really does what the prompt said, so that an agent
cannot pass by writing tests that agree with its own mistake. Agents never see it: it lives
under `tests/reference/`, which the test collection skips, and it is copied into a throw-away
project as `test/test.py` only by the integration tests (and the recorded demo run).

It assumes only the prompt, an 8-bit counter on `uo_out` with a count enable on `ui_in[0]`:

* while `ui_in[0]` is 1 the count goes up by one on every clock;
* while `ui_in[0]` is 0 it holds its value;
* it wraps from 255 back to 0.

It does not assume a reset value (the count is read after reset), does not rely on any other
`ui_in` bit (they are driven randomly) and drives `uio_in` to 0. Inputs change on the falling
clock edge and the output is read on the next falling edge, so results do not depend on how a
simulator orders events at the rising edge.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

HOLD_CYCLES = 20
COUNT_CYCLES = 300  # more than 256, so the wrap from 255 to 0 is always crossed
MIXED_CYCLES = 600


def drive_ui(dut, enable, rng):
    """ui_in[0] = enable; the other bits are random and must not matter."""
    dut.ui_in.value = (rng.getrandbits(7) << 1) | enable


async def start(dut, rng):
    """Start the clock, hold reset low for a few cycles, release it and return the count."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.ena.value = 1
    dut.uio_in.value = 0
    drive_ui(dut, 0, rng)
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)
    return int(dut.uo_out.value)


@cocotb.test()
async def test_counter_reference(dut):
    rng = random.Random(20261112)
    count = await start(dut, rng)

    # Disabled: the count holds, whatever the other input bits do.
    for cycle in range(HOLD_CYCLES):
        drive_ui(dut, 0, rng)
        await FallingEdge(dut.clk)
        got = int(dut.uo_out.value)
        assert got == count, f"disabled, cycle {cycle}: expected it to hold {count}, got {got}"

    # Enabled: +1 on every clock, wrapping from 255 to 0.
    wrapped = False
    for cycle in range(COUNT_CYCLES):
        drive_ui(dut, 1, rng)
        await FallingEdge(dut.clk)
        count = (count + 1) % 256
        wrapped = wrapped or count == 0
        got = int(dut.uo_out.value)
        assert got == count, f"enabled, cycle {cycle}: expected {count}, got {got}"
    assert wrapped, "the test never crossed the wrap from 255 to 0"

    # Enable switching on and off at random: it counts only on the clocks where it is 1.
    for cycle in range(MIXED_CYCLES):
        enable = rng.getrandbits(1)
        drive_ui(dut, enable, rng)
        await FallingEdge(dut.clk)
        count = (count + enable) % 256
        got = int(dut.uo_out.value)
        assert got == count, f"mixed, cycle {cycle} (enable {enable}): expected {count}, got {got}"
