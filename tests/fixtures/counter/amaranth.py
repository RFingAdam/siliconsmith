# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""8-bit counter in Amaranth: counts up while ui_in[0] is 1, holds while it is 0."""

from amaranth.hdl import ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ui_in: In(8)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        # Tiny Tapeout gives an active-low reset; build the sync domain from the pins.
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]

        count = Signal(8)
        with m.If(self.ui_in[0]):
            m.d.sync += count.eq(count + 1)
        m.d.comb += self.uo_out.eq(count)
        return m


def top() -> wiring.Component:
    return Core()
