# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
"""REVIEWER: genuine MyHDL always_comb whose loop reads t in iteration 0 and writes it in 1."""

from myhdl import Signal, always_comb, always_seq, block, intbv, modbv


@block
def gloopit(clk, rst_n, ui_in, uo_out):
    cnt = Signal(modbv(0)[8:])
    t = Signal(intbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            cnt.next = cnt + 1

    @always_comb
    def pick():
        uo_out.next = 0
        for i in range(2):
            if i == 1:
                t.next = cnt
            else:
                uo_out.next = t

    return seq, pick
