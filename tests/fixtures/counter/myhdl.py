# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""8-bit counter in MyHDL: counts up while ui_in[0] is 1, holds while it is 0."""

from myhdl import Signal, always_comb, always_seq, block, modbv


@block
def counter(clk, rst_n, ui_in, uo_out):
    # modbv wraps from 255 back to 0, like the hardware register does.
    count = Signal(modbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            count.next = count + 1

    @always_comb
    def drive():
        uo_out.next = count

    return seq, drive
