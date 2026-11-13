# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""A register with init=1 in an async-reset clock domain whose reset is never driven.

Reset (rst_n) does not set it; it reaches uo_out[0] only 42 cycles after reset."""

from amaranth.hdl import C, ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    ui_in: In(8)
    uo_out: Out(8)
    uio_out: Out(8)
    uio_oe: Out(8)
    clk: In(1)
    rst_n: In(1)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.domains.free = fd = ClockDomain("free", async_reset=True)
        m.d.comb += [
            cd.clk.eq(self.clk),
            cd.rst.eq(~self.rst_n),
            fd.clk.eq(self.clk),
            fd.rst.eq(self.ui_in[7] & C(0, 1)),
        ]
        cnt = Signal(6)
        flag = Signal(init=1)
        with m.If(cnt != 40):
            m.d.sync += cnt.eq(cnt + 1)
        with m.If((cnt == 40) & self.ui_in[0]):
            m.d.free += flag.eq(~flag)
        m.d.comb += [self.uo_out.eq((cnt == 40) & flag), self.uio_out.eq(0), self.uio_oe.eq(0)]
        return m


def top():
    return Core()
