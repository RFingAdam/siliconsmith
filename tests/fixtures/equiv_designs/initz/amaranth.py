# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""A free-running counter in a reset-less domain: Amaranth starts it at 0, silicon cannot."""

from amaranth.hdl import ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync", reset_less=True)
        m.d.comb += cd.clk.eq(self.clk)
        cnt = Signal(8)
        m.d.sync += cnt.eq(cnt + 1)
        m.d.comb += self.uo_out.eq(cnt)
        return m


def top() -> wiring.Component:
    return Core()
