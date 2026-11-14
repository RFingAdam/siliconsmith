# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Moore detector for 1,0,1,1 on ui_in[0] (overlapping); uo_out[0] = 1 in the match state."""

from amaranth.hdl import ClockDomain, Module
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
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]
        x = self.ui_in[0]
        with m.FSM(init="IDLE") as fsm:
            with m.State("IDLE"), m.If(x):
                m.next = "S1"
            with m.State("S1"), m.If(~x):
                m.next = "S10"
            with m.State("S10"):
                with m.If(x):
                    m.next = "S101"
                with m.Else():
                    m.next = "IDLE"
            with m.State("S101"):
                with m.If(x):
                    m.next = "S1011"
                with m.Else():
                    m.next = "S10"
            with m.State("S1011"):
                with m.If(x):
                    m.next = "S1"
                with m.Else():
                    m.next = "S10"
        m.d.comb += [self.uo_out.eq(fsm.ongoing("S1011")), self.uio_out.eq(0), self.uio_oe.eq(0)]
        return m


def top():
    return Core()
