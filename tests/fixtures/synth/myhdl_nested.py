"""counter with a latch inside a sub-block"""

from myhdl import Signal, always_comb, always_seq, block, modbv


@block
def keeper(en, d, q):
    @always_comb
    def remember():
        if en:
            q.next = d

    return remember


@block
def counter(clk, rst_n, ui_in, uo_out):
    count = Signal(modbv(0)[8:])
    held = Signal(modbv(0)[8:])
    gate = Signal(bool(0))

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            count.next = count + 1

    @always_comb
    def pick():
        gate.next = ui_in[7]

    k = keeper(gate, count, held)

    @always_comb
    def drive():
        uo_out.next = held

    return seq, pick, k, drive
