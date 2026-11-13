# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""MyHDL `@always(b0)` through an alias (`from myhdl import always as alw`): a level list that
misses `cnt`, which the process reads. The equivalence check judges the emitted Verilog, so the
spelling does not matter."""

from myhdl import Signal, always_comb, always_seq, block, modbv
from myhdl import always as alw


@block
def myalias(clk, rst_n, ui_in, uo_out):
    cnt = Signal(modbv(0)[8:])
    y = Signal(modbv(0)[8:])
    b0 = Signal(bool(0))

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        cnt.next = cnt + 1

    @always_comb
    def bit0():
        b0.next = ui_in[0]

    @alw(b0)
    def pick():
        y.next = cnt

    @always_comb
    def drive():
        uo_out.next = y

    return seq, bit0, pick, drive
