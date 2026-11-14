# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""blinkmux in Amaranth (round 15 p14): an LED on uo_out[7] that toggles every clock, and uo_out[0]
that follows ui_in[0] while the LED is on and ui_in[1] while it is off. Must be equivalent."""

from amaranth.hdl import Cat, ClockDomain, Module, Mux, Signal
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
        # an LED that blinks every clock, and a pin that follows one of two switches
        blink = Signal()
        m.d.sync += blink.eq(~blink)
        m.d.comb += self.uo_out.eq(Cat(Mux(blink, self.ui_in[0], self.ui_in[1]), Signal(6), blink))
        return m


def top() -> wiring.Component:
    return Core()
