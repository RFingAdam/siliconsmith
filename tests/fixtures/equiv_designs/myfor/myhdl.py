# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""MyHDL `@always_comb` with a for loop and an integer variable: counts the set bits of ui_in."""

from myhdl import Signal, always_comb, always_seq, block, intbv, modbv


@block
def myfor(clk, rst_n, ui_in, uo_out):
    q = Signal(modbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        q.next = q + ui_in

    @always_comb
    def cnt():
        n = intbv(0)[8:]
        for i in range(8):
            if q[i]:
                n[:] = n + 1
        uo_out.next = n

    return seq, cnt
