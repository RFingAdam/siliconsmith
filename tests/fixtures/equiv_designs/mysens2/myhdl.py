# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""MyHDL `@always(b0)`: a process that waits for a level signal and reads a counter too."""

from myhdl import Signal, always, always_comb, always_seq, block, modbv


@block
def mysens2(clk, rst_n, ui_in, uo_out):
    cnt = Signal(modbv(0)[8:])
    y = Signal(modbv(0)[8:])
    b0 = Signal(bool(0))

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        cnt.next = cnt + 1

    @always_comb
    def bit0():
        b0.next = ui_in[0]

    @always(b0)
    def pick():
        y.next = cnt

    @always_comb
    def drive():
        uo_out.next = y

    return seq, bit0, pick, drive
