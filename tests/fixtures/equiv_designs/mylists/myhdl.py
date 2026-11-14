# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""MyHDL `@always_comb` that reads a list of signals (written `mem[0], mem[1], ...` in the list)."""

from myhdl import Signal, always_comb, always_seq, block, intbv, modbv


@block
def mylists(clk, rst_n, ui_in, uo_out):
    mem = [Signal(modbv(0)[8:]) for _ in range(4)]
    sel = Signal(intbv(0)[2:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        for i in range(4):
            mem[i].next = mem[i] ^ ui_in

    @always_comb
    def rd():
        sel.next = ui_in[2:0]
        v = intbv(0)[8:]
        v[:] = mem[sel]
        if v > 3:
            uo_out.next = v + mem[1]
        else:
            uo_out.next = mem[3]

    return seq, rd
