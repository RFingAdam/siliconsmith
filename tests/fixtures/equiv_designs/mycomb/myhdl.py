# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""MyHDL `@always_comb` with an `if`: the list MyHDL writes names everything the block reads."""

from myhdl import Signal, always_comb, always_seq, block, intbv


@block
def mycomb(clk, rst_n, ui_in, uo_out):
    q = Signal(intbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        q.next = ui_in

    @always_comb
    def pick():
        if ui_in[0]:
            uo_out.next = q
        else:
            uo_out.next = ~q

    return seq, pick
