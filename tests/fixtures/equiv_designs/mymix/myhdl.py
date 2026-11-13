# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""MyHDL `@always_comb` with an enum state, bool operators, slices, shifts and a list of signals."""

from myhdl import Signal, always_comb, always_seq, block, concat, enum, intbv, modbv


@block
def mymix(clk, rst_n, ui_in, uo_out):
    t = enum("A", "B", "C")
    st = Signal(t.A)
    q = Signal(modbv(0)[8:])
    mem = [Signal(modbv(0)[8:]) for _ in range(4)]
    idx = Signal(intbv(0)[2:])
    o = Signal(intbv(0)[8:])
    w = Signal(intbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        q.next = q + 1
        if st == t.A:
            st.next = t.B
        elif st == t.B:
            st.next = t.C
        else:
            st.next = t.A
        for i in range(4):
            mem[i].next = q ^ mem[i]

    @always_comb
    def comb():
        idx.next = ui_in[2:0]
        v = intbv(0)[8:]
        x = ui_in[0] and q[1]
        y = q[2] or ui_in[1]
        if x:
            v[:] = q
        else:
            v[:] = ui_in
        v[4:2] = ui_in[2:0]
        if st == t.A:
            w.next = v + mem[idx]
        elif st == t.B:
            w.next = concat(v[4:0], q[8:4])
        else:
            if y:
                w.next = v << 1
            else:
                w.next = v
        if ui_in[7]:
            o.next = w >> 2
        else:
            o.next = ~w

    @always_comb
    def out():
        uo_out.next = o

    return seq, comb, out
