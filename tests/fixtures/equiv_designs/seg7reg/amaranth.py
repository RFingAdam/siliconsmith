# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""seg7reg in Amaranth: a seven-segment decoder whose table is an Array of constants (a small
ROM), read into a register every clock (round 14 a19). The hex digit on ui_in[3:0] lights the
segments on uo_out one clock later; bit 7 (the dot) is off in every entry. Must be equivalent."""

from amaranth.hdl import Array, ClockDomain, Const, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out

# the segments of the hex digits 0 to f (bit 0 is segment a, bit 7 the dot)
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
        rom = Array(Const(v, 8) for v in TABLE)
        segments = Signal(8)  # 0 after reset: every segment off
        m.d.sync += segments.eq(rom[self.ui_in[0:4]])
        m.d.comb += self.uo_out.eq(segments ^ self.uio_in)
        return m


def top() -> wiring.Component:
    return Core()
