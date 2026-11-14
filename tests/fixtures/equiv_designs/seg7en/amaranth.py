# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""seg7en in Amaranth: a seven-segment decoder written as an `m.Switch` with one case per hex
digit, registered on an enable (round 14 v60 and a17). While ui_in[0] is 1 the register takes the
segments of the digit on uio_in[3:0], while it is 0 the display holds; bit 7 (the dot) is off in
every entry. Must be equivalent."""

from amaranth.hdl import ClockDomain, Module, Signal
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
        # the table: one case per digit
        decoded = Signal(8)
        with m.Switch(self.uio_in[0:4]):
            for digit, lit in enumerate(TABLE):
                with m.Case(digit):
                    m.d.comb += decoded.eq(lit)
        # the register: a new digit only while the enable is 1
        segments = Signal(8)  # 0 after reset: every segment off
        with m.If(self.ui_in[0]):
            m.d.sync += segments.eq(decoded)
        m.d.comb += self.uo_out.eq(segments)
        return m


def top() -> wiring.Component:
    return Core()
