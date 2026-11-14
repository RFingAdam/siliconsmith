# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""seg7mux in MyHDL (round 15 q14): a two-digit multiplexed seven-segment display. `sel` toggles
every clock, an `@always_comb` block picks the nibble, a tuple of constants gives the segments,
and uo_out[7] says which digit is lit. Must be equivalent."""

from myhdl import Signal, always_comb, always_seq, block, concat, intbv

SEGMENTS = (0x3F, 0x06, 0x5B, 0x4F, 0x66, 0x6D, 0x7D, 0x07,
            0x7F, 0x6F, 0x77, 0x7C, 0x39, 0x5E, 0x79, 0x71)  # fmt: skip


@block
def seg7mux(clk, rst_n, ui_in, uo_out):
    sel = Signal(bool(0))
    nib = Signal(intbv(0)[4:])
    seg = Signal(intbv(0)[7:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        sel.next = not sel

    @always_comb
    def pick():
        if sel:
            nib.next = ui_in[8:4]
        else:
            nib.next = ui_in[4:0]

    @always_comb
    def decode():
        seg.next = SEGMENTS[int(nib)]

    @always_comb
    def drive():
        uo_out.next = concat(sel, seg)

    return seq, pick, decode, drive
