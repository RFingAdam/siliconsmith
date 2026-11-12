# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""One cocotb 2.x test that never touches rst_n: count five clocks from power-up.

Run on the seeded no-reset counter: RTL passes (the counter starts at its initial value), the
gates start unknown and fail. Because rst_n is never driven there is no reset to wait for, so the
first difference must still be reported.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


@cocotb.test()
async def test_counts_without_touching_reset(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 1
    await ClockCycles(dut.clk, 5, rising=True)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 0
    assert int(dut.uo_out.value) == 5
