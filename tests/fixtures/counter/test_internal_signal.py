# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One cocotb 2.x test that is right on the RTL but reads a name the gates do not have.

The design resets properly. The test holds reset, releases it, and then reads the counter's
internal register, which only exists in the RTL. The gate-level run fails for that reason alone;
the chip's pins match the RTL after reset.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge


@cocotb.test()
async def test_reads_an_internal_register(dut):
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
    assert int(dut.user_project.core.count.value) == 5
