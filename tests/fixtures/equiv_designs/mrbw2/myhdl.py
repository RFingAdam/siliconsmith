# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
"""REVIEWER: genuine MyHDL always_comb that reads t before it writes t (an if forces a block)."""

from myhdl import Signal, always_comb, always_seq, block, intbv, modbv


@block
def mrbw2(clk, rst_n, ui_in, uo_out):
    cnt = Signal(modbv(0)[8:])
    t = Signal(intbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            cnt.next = cnt + 1

    @always_comb
    def pick():
        if ui_in[1]:
            uo_out.next = t
        else:
            uo_out.next = 0
        t.next = cnt

    return seq, pick
