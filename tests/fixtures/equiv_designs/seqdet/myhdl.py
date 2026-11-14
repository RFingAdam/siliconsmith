# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Sequence detector in MyHDL: uo_out[0] is 1 for one cycle after ui_in[0] showed 1, 0, 1, 1."""

from myhdl import Signal, always_comb, always_seq, block, enum


@block
def seqdet(clk, rst_n, ui_in, uo_out):
    t_state = enum("IDLE", "S1", "S10", "S101", "S1011")
    state = Signal(t_state.IDLE)

    @always_seq(clk.posedge, reset=rst_n)
    def fsm():
        x = ui_in[0]
        if state == t_state.IDLE:
            state.next = t_state.S1 if x else t_state.IDLE
        elif state == t_state.S1:
            state.next = t_state.S1 if x else t_state.S10
        elif state == t_state.S10:
            state.next = t_state.S101 if x else t_state.IDLE
        elif state == t_state.S101:
            state.next = t_state.S1011 if x else t_state.S10
        else:
            state.next = t_state.S1 if x else t_state.S10

    @always_comb
    def drive():
        uo_out.next = 1 if state == t_state.S1011 else 0

    return fsm, drive
