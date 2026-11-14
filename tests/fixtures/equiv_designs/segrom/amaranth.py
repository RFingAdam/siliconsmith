# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A seven-segment table as an Amaranth Array of constants, registered on an enable; bit 7 is 0 in
every entry (round 14 a13)."""

from amaranth.hdl import Array, ClockDomain, Const, Module, Mux, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

# the segments of the hex digits 0 to f (bit 7, the dot, is off in every entry)
TABLE = bytes.fromhex("3f065b4f666d7d077f6f777c395e7971")


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
        ui, uio = self.ui_in, self.uio_in
        rom = Array(Const(v, 8) for v in TABLE)
        q = Signal(8)
        with m.If(ui[4]):
            m.d.sync += q.eq(rom[ui[0:4]])
        m.d.comb += self.uo_out.eq(Mux(ui[5], q, rom[uio[0:4]]))
        return m


def top() -> wiring.Component:
    return Core()
