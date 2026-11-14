# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One cocotb 2.x test for the seeded no-reset counter: count five clocks from power-up.

It holds reset low like any test would, but the design ignores it and starts from its initial
value, so RTL passes and the gates (which start unknown) fail.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


@cocotb.test()
async def test_counts_from_power_up(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    dut.ui_in.value = 1
    await ClockCycles(dut.clk, 5, rising=True)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 0
    assert int(dut.uo_out.value) == 5
