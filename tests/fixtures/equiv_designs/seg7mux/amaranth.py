# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""seg7mux in Amaranth (round 15 p12): the two-digit multiplexed display with an `Array` of
constants as the table and a `Mux` that picks the nibble. Must be equivalent."""

from amaranth.hdl import Array, Cat, ClockDomain, Const, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

TABLE = [0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07,
         0x7F, 0x6F, 0x77, 0x7C, 0x39, 0x5E, 0x79, 0x71]  # fmt: skip


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ui_in: In(8)
    uio_in: In(8)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]
        rom = Array(Const(v, 7) for v in TABLE)
        sel = Signal()
        m.d.sync += sel.eq(~sel)
        nib = Mux(sel, self.ui_in[4:8], self.ui_in[0:4])
        m.d.comb += self.uo_out.eq(Cat(rom[nib], sel))
        return m


def top() -> wiring.Component:
    return Core()
