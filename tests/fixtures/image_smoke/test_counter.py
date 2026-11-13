# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
"""cocotb 2.x test for counter.v: counts while enabled, holds while not, wraps at 255."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer


async def reset(dut):
    dut.en.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 1)


async def cycles(dut, n):
    """Wait n clock edges, then 1 ns more, so registers that updated on the last edge are read."""
    await ClockCycles(dut.clk, n)
    await Timer(1, unit="ns")


@cocotb.test()
async def counts_and_holds(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    assert int(dut.count.value) == 0
    dut.en.value = 1
    await cycles(dut, 5)
    assert int(dut.count.value) == 5
    dut.en.value = 0
    await cycles(dut, 4)
    assert int(dut.count.value) == 5


@cocotb.test()
async def wraps(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut)
    dut.en.value = 1
    await cycles(dut, 256)
    assert int(dut.count.value) == 0
