# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Amaranth 16x8 memory that starts with contents (reset cannot set a memory)."""

from amaranth.hdl import ClockDomain, Module
from amaranth.lib import wiring
from amaranth.lib.memory import Memory
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    ui_in: In(8)
    uio_in: In(8)
    uo_out: Out(8)
    uio_out: Out(8)
    uio_oe: Out(8)
    clk: In(1)
    rst_n: In(1)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]
        m.submodules.mem = mem = Memory(shape=8, depth=16, init=list(range(16)))
        wp = mem.write_port()
        rp = mem.read_port(transparent_for=(wp,) if 0 else ())
        m.d.comb += [
            wp.addr.eq(self.uio_in[0:4]),
            wp.data.eq(self.ui_in),
            wp.en.eq(self.uio_in[7]),
            rp.addr.eq(self.ui_in[0:4]),
            self.uo_out.eq(rp.data),
            self.uio_out.eq(0),
            self.uio_oe.eq(0),
        ]
        return m


def top():
    return Core()
