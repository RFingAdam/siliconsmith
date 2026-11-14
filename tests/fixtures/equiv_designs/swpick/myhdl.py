# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""swpick in MyHDL: a counter picked by a 4-way switch on ui_in; must be equivalent."""

from myhdl import Signal, always_comb, always_seq, block, concat, intbv, modbv


@block
def swpick(clk, rst_n, ui_in, uo_out):
    cnt = Signal(modbv(0)[8:])
    y = Signal(intbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            cnt.next = cnt + 1

    @always_comb
    def pick():
        if ui_in[3:1] == 0:
            y.next = cnt
        elif ui_in[3:1] == 1:
            y.next = ~cnt
        elif ui_in[3:1] == 2:
            y.next = concat(cnt[7:0], False)
        else:
            y.next = concat(False, cnt[8:1])

    @always_comb
    def drive():
        uo_out.next = y

    return seq, pick, drive
