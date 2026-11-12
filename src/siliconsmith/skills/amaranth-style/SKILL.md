---
name: amaranth-style
description: Use when writing the design in Amaranth (Python HDL, version 0.5) so that emit_verilog accepts it as a Tiny Tapeout core.
---

# Amaranth style for this server

Amaranth is a Python library for describing hardware. Your Python file is a description, not a program that runs on the chip: `emit_verilog` runs it in a container and converts it to Verilog. Fenced examples below are tested against the real tools: each is emitted and linted, and simulated on Icarus and Verilator with the tests in `explain('cocotb2-tests')`.

## The entry point

`hdl/<name>.py` must define a module-level `def top()` that returns a `wiring.Component`. The signature declares only the Tiny Tapeout pins you use, with their exact names: `In(1)` for `clk`, `rst_n`, `ena`; `In(8)` for `ui_in`, `uio_in`; `Out(8)` for `uo_out`, `uio_out`, `uio_oe`. The tool gives the generated module the project name. See `explain('tt-rules')` for the pin list.

## The clock domain

Amaranth's default reset is an active-high port called `rst`. Tiny Tapeout has no such pin (it has `rst_n`, active low), so the conversion would add a port the wrapper does not know and `emit_verilog` would fail. Always build the `sync` domain yourself from the pins:

```python
m.domains.sync = cd = ClockDomain("sync")
m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]
```

Everything you write with `m.d.sync` then uses `clk` and resets when `rst_n` is 0. In RF terms, this is wiring up your sampling clock by hand instead of letting the tool invent one.

## Counter

A counter is a phase accumulator: a register that adds a fixed step on every clock. Here it adds 1 while `ui_in[0]` is 1 and wraps from 255 to 0, because assigning a 9-bit sum to an 8-bit signal drops the top bit.

```python example=amaranth-counter project=counter frontend=amaranth file=hdl/counter.py tests=cocotb-idioms
from amaranth.hdl import ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ui_in: In(8)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]

        count = Signal(8)
        with m.If(self.ui_in[0]):
            m.d.sync += count.eq(count + 1)
        m.d.comb += self.uo_out.eq(count)
        return m


def top() -> wiring.Component:
    return Core()
```

## State machine

Use `m.FSM()`. Each `m.State` block runs while the machine is in that state; `m.next = "NAME"` picks the state after the next clock. The first state listed is the reset state. This one waits for `ui_in[0]`, then reports `busy` on `uo_out[0]` for four clocks.

```python example=amaranth-fsm project=pulse frontend=amaranth file=hdl/pulse.py tests=fsm-test
from amaranth.hdl import ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ui_in: In(8)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]

        left = Signal(3)
        with m.FSM() as fsm:
            with m.State("IDLE"):
                with m.If(self.ui_in[0]):
                    m.d.sync += left.eq(3)
                    m.next = "RUN"
            with m.State("RUN"):
                m.d.sync += left.eq(left - 1)
                with m.If(~left.any()):
                    m.next = "IDLE"
        # Drive the output from the state register, not from a comb line inside a state.
        m.d.comb += self.uo_out.eq(fsm.ongoing("RUN"))
        return m


def top() -> wiring.Component:
    return Core()
```

## Shift register

A shift register moves every bit one place per clock and takes a new bit in at one end. `Cat` joins signals with the first argument as the lowest bit, so this one shifts left and takes `ui_in[0]` in at bit 0.

```python example=amaranth-shift project=shifter frontend=amaranth file=hdl/shifter.py tests=shift-test
from amaranth.hdl import Cat, ClockDomain, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In, Out


class Core(wiring.Component):
    clk: In(1)
    rst_n: In(1)
    ui_in: In(8)
    uo_out: Out(8)

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = cd = ClockDomain("sync")
        m.d.comb += [cd.clk.eq(self.clk), cd.rst.eq(~self.rst_n)]

        bits = Signal(8)
        m.d.sync += bits.eq(Cat(self.ui_in[0], bits[:7]))
        m.d.comb += self.uo_out.eq(bits)
        return m


def top() -> wiring.Component:
    return Core()
```

## Common mistakes

- Forgetting the clock domain: the conversion adds a `rst` port and `emit_verilog` rejects the ports.
- Writing a register with `m.d.comb`, or a wire with `m.d.sync`. `m.d.comb` is for values that follow their inputs at once; `m.d.sync` is for values that change on the clock edge.
- Driving one signal from both `m.d.comb` and `m.d.sync`, or from two places. Each signal gets one driver.
- Widths: `count + 1` is one bit wider than `count`, and assigning it back truncates silently. That is right for a wrapping counter and wrong if you wanted a saturating one. A `Signal(3)` cannot hold 8.
- Forgetting to assign an output. Assign every output you declare (`explain('tt-rules')`).
- Naming a pin differently: the names must match the Tiny Tapeout pins exactly. (Declaring a pin you never use is accepted; it is only clutter.)
- Python `if` on a signal. Use `with m.If(...)`: a Python `if` is decided once, while the Python runs, not by the hardware.

## Width and simulator traps

- Comparing a signal with a literal that needs fewer bits than the signal fails lint with WIDTHEXPAND: `left == 1` on a 3-bit `left` fails, and so does `left == 2`, `left == 3` or `left > 1`. `left == 0` fails with WIDTHTRUNC. A sized `Const(1, 3)` does not help, because the conversion shrinks the constant back. Compare with zero as `~left.any()`, and with another value `v` as `~(left ^ v).any()`; both are clean. A literal that fills the width (`left == 7` on 3 bits) is fine.
- Driving an output from a `m.d.comb` line inside `m.If`, `m.Switch` or `m.FSM` that depends only on registers that never change after reset. Icarus then leaves the output unknown (`x`) until those registers first change, while Verilator shows 0, so the two simulators disagree. Drive outputs from a register, from `fsm.ongoing("STATE")`, or from a plain `m.d.comb` line at the top level.

## Latches

Amaranth cannot infer a latch: a combinational value that you do not assign on some path takes its default (0 unless you say otherwise), so you get gates, not a memory. The latch rules in `explain('tt-rules')` matter for Verilog and MyHDL; in Amaranth the lint step will not flag them.
