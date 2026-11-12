# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""16-bit shift register in Amaranth; only the parity of its bits leaves the chip on uo_out[0]."""

from amaranth.hdl import Cat, ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ui_in: In(8)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]

        sr = Signal(16)
        m.d.sync += sr.eq(Cat(self.ui_in[0], sr[:15]))
        m.d.comb += self.uo_out.eq(sr.xor())
        return m


def top() -> wiring.Component:
    return Core()
