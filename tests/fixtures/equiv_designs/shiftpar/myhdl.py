# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""16-bit shift register in MyHDL; only the parity of its 16 bits leaves the chip on uo_out[0]."""

from myhdl import Signal, always_comb, always_seq, block, concat, intbv


@block
def shiftpar(clk, rst_n, ui_in, uo_out):
    sr = Signal(intbv(0)[16:])
    par = Signal(bool(0))

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        sr.next = concat(sr[15:0], ui_in[0])

    @always_comb
    def parity():
        # Written out bit by bit: MyHDL turns a loop variable into a 32-bit integer.
        par.next = (
            sr[0] ^ sr[1] ^ sr[2] ^ sr[3] ^ sr[4] ^ sr[5] ^ sr[6] ^ sr[7]
            ^ sr[8] ^ sr[9] ^ sr[10] ^ sr[11] ^ sr[12] ^ sr[13] ^ sr[14] ^ sr[15]
        )  # fmt: skip

    @always_comb
    def drive():
        uo_out.next = concat(intbv(0)[7:], par)

    return seq, parity, drive
