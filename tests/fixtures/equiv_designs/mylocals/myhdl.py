# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""MyHDL `@always_comb` with a local variable, an `if` chain and bit slices."""

from myhdl import Signal, always_comb, always_seq, block, intbv, modbv


@block
def mylocals(clk, rst_n, ui_in, uo_out):
    q = Signal(modbv(0)[8:])
    r = Signal(intbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        q.next = q + 1

    @always_comb
    def blend():
        b = intbv(0)[8:]
        if ui_in[7]:
            b[:] = q
        elif ui_in[4:0] == 3:
            b[:] = ui_in ^ q
        elif ui_in[4:0] > 5:
            b[:] = ~q
        else:
            b[:] = ui_in
        r.next = b

    @always_comb
    def out():
        uo_out.next = r ^ ui_in

    return seq, blend, out
