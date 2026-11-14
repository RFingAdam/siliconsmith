# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Amaranth counter in an async-reset domain (legit)."""

from amaranth.hdl import ClockDomain, Module, Signal
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
        m.domains.sync = cd = ClockDomain("sync", async_reset=True)
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]
        count = Signal(8)
        m.d.sync += count.eq(count + 1)
        m.d.comb += [self.uo_out.eq(count), self.uio_out.eq(0), self.uio_oe.eq(0)]
        return m


def top():
    return Core()
