# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""cocotb 2.x tests for the 8-bit counter (reset, count, hold, wrap).

Inputs change on the falling clock edge and the output is read on the next falling edge, so the
result does not depend on how a simulator orders events at the rising edge.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


async def start(dut):
    """Start the clock and hold reset for a few cycles; leave the counter disabled."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def run_enabled(dut, cycles):
    """Count with ui_in[0] = 1 for `cycles` rising edges, then stop and read the output."""
    await FallingEdge(dut.clk)
    dut.ui_in.value = 1
    await ClockCycles(dut.clk, cycles, rising=True)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 0
    return int(dut.uo_out.value)


@cocotb.test()
async def test_reset_clears_count(dut):
    await start(dut)
    await FallingEdge(dut.clk)
    assert int(dut.uo_out.value) == 0


@cocotb.test()
async def test_counts_when_enabled(dut):
    """Covers: R1"""
    await start(dut)
    count = await run_enabled(dut, 10)
    assert count == 10, f"uo_out after 10 enabled clocks: expected 10, got {count}"


@cocotb.test()
async def test_holds_when_disabled(dut):
    """Covers: R2"""
    await start(dut)
    assert await run_enabled(dut, 7) == 7
    await ClockCycles(dut.clk, 20)
    assert int(dut.uo_out.value) == 7


@cocotb.test()
async def test_wraps_at_255(dut):
    """Covers: R3"""
    await start(dut)
    assert await run_enabled(dut, 255) == 255
    assert await run_enabled(dut, 1) == 0
