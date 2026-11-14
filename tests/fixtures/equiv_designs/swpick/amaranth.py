# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""swpick in Amaranth: a counter that reset does not set, picked by a 4-way switch on ui_in.
The proof's power-up base case must call it equivalent (it once spread x gate by gate)."""

from amaranth.hdl import ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ena: In(1)
    ui_in: In(8)
    uio_in: In(8)
    uo_out: Out(8)
    uio_out: Out(8)
    uio_oe: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]
        cnt = Signal(8)
        with m.If(self.ui_in[0]):
            m.d.sync += cnt.eq(cnt + 1)
        y = Signal(8)
        with m.Switch(self.ui_in[1:3]):
            with m.Case(0):
                m.d.comb += y.eq(cnt)
            with m.Case(1):
                m.d.comb += y.eq(~cnt)
            with m.Case(2):
                m.d.comb += y.eq(cnt << 1)
            with m.Default():
                m.d.comb += y.eq(cnt >> 1)
        m.d.comb += [self.uo_out.eq(y), self.uio_out.eq(0), self.uio_oe.eq(0)]
        return m


def top() -> wiring.Component:
    return Core()
