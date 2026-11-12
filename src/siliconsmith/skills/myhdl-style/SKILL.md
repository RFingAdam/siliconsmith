---
name: myhdl-style
description: Use when writing the design in MyHDL (Python HDL, version 0.11) so that emit_verilog accepts it as a Tiny Tapeout core.
---

# MyHDL style for this server

MyHDL describes hardware as Python functions. `emit_verilog` runs your file in a container and converts it to Verilog. Fenced examples below are tested against the real tools: each is emitted and linted, and simulated on Icarus and Verilator with the tests in `explain('cocotb2-tests')`.

## The entry point

`hdl/<name>.py` must define `@block def <name>(...)`, named exactly like the project. Its parameters are Tiny Tapeout pin names and nothing else: `clk`, `rst_n`, `ena`, `ui_in`, `uio_in`, `uo_out`, `uio_out`, `uio_oe` (`explain('tt-rules')`). The tool creates the signals for you: 8-bit buses are `Signal(intbv(0)[8:])`, and `rst_n` is a `ResetSignal(active=0, isasync=False)`, a synchronous active-low reset.

Only list the pins you use. MyHDL warns about a port that nothing reads or drives (a `ToVerilogWarning`), and this server treats that warning as an error. A pin you leave out is handled by the generated wrapper.

## Counter

A counter is a phase accumulator. Use `modbv` (modulo bit vector) to state that the count wraps from 255 to 0; a plain `intbv` is a bounded number, and MyHDL's own simulator would stop on overflow. This server simulates the converted Verilog, where both wrap the same way, so `modbv` is about stating your intent. `@always_seq(clk.posedge, reset=rst_n)` makes a clocked block that is reset by `rst_n`. `@always_comb` makes combinational logic.

```python example=myhdl-counter project=counter frontend=myhdl file=hdl/counter.py tests=cocotb-idioms
from myhdl import Signal, always_comb, always_seq, block, modbv


@block
def counter(clk, rst_n, ui_in, uo_out):
    count = Signal(modbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if ui_in[0]:
            count.next = count + 1

    @always_comb
    def drive():
        uo_out.next = count

    return seq, drive
```

Assign with `.next`: `count.next = ...`. Reading uses the plain signal. A register that is assigned inside `always_seq` is reset to its initial value, here 0.

## State machine

Use `enum` for the states. The reset state is the value you give `Signal(...)`, here `t_state.IDLE`. This one waits for `ui_in[0]`, then reports `busy` on `uo_out[0]` for four clocks.

```python example=myhdl-fsm project=pulse frontend=myhdl file=hdl/pulse.py tests=fsm-test
from myhdl import Signal, always_comb, always_seq, block, enum, intbv


@block
def pulse(clk, rst_n, ui_in, uo_out):
    t_state = enum("IDLE", "RUN")
    state = Signal(t_state.IDLE)
    left = Signal(intbv(0)[3:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        if state == t_state.IDLE:
            if ui_in[0]:
                left.next = 4
                state.next = t_state.RUN
        else:
            left.next = left - 1
            if left == 1:
                state.next = t_state.IDLE

    @always_comb
    def drive():
        if state == t_state.RUN:
            uo_out.next = 1
        else:
            uo_out.next = 0

    return seq, drive
```

## Shift register, slices and concatenation

`sig[7:0]` is a slice of 7 bits (bit 6 down to bit 0): the upper index is excluded, as in Python. `sig[0]` is one bit. `concat(a, b)` joins signals with `a` as the high part. This shift register moves left and takes `ui_in[0]` in at bit 0.

```python example=myhdl-shift project=shifter frontend=myhdl file=hdl/shifter.py tests=shift-test
from myhdl import Signal, always_comb, always_seq, block, concat, modbv


@block
def shifter(clk, rst_n, ui_in, uo_out):
    bits = Signal(modbv(0)[8:])

    @always_seq(clk.posedge, reset=rst_n)
    def seq():
        bits.next = concat(bits[7:0], ui_in[0])

    @always_comb
    def drive():
        uo_out.next = bits

    return seq, drive
```

## Common mistakes

- Listing a pin you never use (or leaving an output undriven): a `ToVerilogWarning`, which is an error here.
- A block name that differs from the project name, or pin parameters with other names.
- Using `intbv` for a counter that wraps. Use `modbv` to state the wrap; MyHDL's own simulator would stop on overflow, though this server's simulation of the converted Verilog does not.
- Forgetting `.next` on an assignment: conversion fails with a ConversionError such as `Local variable may be referenced before assignment`.
- Assigning to a signal from two blocks. Each signal gets one driver.
- Widths: `intbv(0)[3:]` holds 0 to 7. Any width mismatch is a lint failure: assigning the constant 8 to it gives WIDTHTRUNC, and assigning a 3-bit signal to the 8-bit `uo_out` gives WIDTHEXPAND. Size your signals to 8 bits, or slice or `concat` to match.
- Leaving a signal unassigned on some path inside `always_comb` makes a latch. Give every signal a value on every path (`explain('tt-rules')`); `emit_verilog` reports latches as lint errors with a file and line.
- Python features the converter does not support, such as dictionaries, classes, or complex list work inside the block bodies. Keep block bodies simple: `if`, `elif`, `else`, arithmetic, slices.
