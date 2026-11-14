# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""seg7reg in MyHDL: a seven-segment decoder whose table is a tuple of ints (a small ROM), read
into a register every clock (round 14 m16). The hex digit on ui_in[3:0] lights the segments on
uo_out one clock later; bit 7 (the dot) is off in every entry. Must be equivalent."""

from myhdl import Signal, always_comb, always_seq, block, intbv

# the segments of the hex digits 0 to f (bit 0 is segment a, bit 7 the dot)
SEGMENTS = (0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07,
            0x7F, 0x6F, 0x77, 0x7C, 0x39, 0x5E, 0x79, 0x71)  # fmt: skip


@block
def seg7reg(clk, rst_n, ui_in, uo_out):
    shown = Signal(intbv(0)[8:])  # 0 after reset: every segment off

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        shown.next = SEGMENTS[int(ui_in[4:0])]

    @always_comb
    def drive():
        uo_out.next = shown

    return seq, drive
