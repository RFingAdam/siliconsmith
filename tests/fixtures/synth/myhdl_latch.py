"""counter with a latch"""

from myhdl import Signal, always_comb, always_seq, block, modbv


@block
def counter(clk, rst_n, ui_in, uo_out):
    count = Signal(modbv(0)[8:])
    held = Signal(modbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            count.next = count + 1

    @always_comb
    def remember():
        if ui_in[7]:
            held.next = count

    @always_comb
    def drive():
        uo_out.next = held

    return seq, remember, drive
