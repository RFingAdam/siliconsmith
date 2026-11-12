---
name: cocotb2-tests
description: Use when writing or fixing cocotb 2.1 tests for a Siliconsmith project, including converting old cocotb 1.x code.
---

# cocotb 2.1 tests for this server

cocotb lets you write the testbench in Python: it drives the design's pins and checks the outputs, inside a simulator. It is your signal generator and your spectrum analyzer in one script. Tests live in `test/*.py` and go in with `write_test`. The example on this page is tested against the real tools.

## The top level

The design under test is Tiny Tapeout's testbench `tb`, which holds your wrapper. The handle `dut` has the Tiny Tapeout pins: `dut.clk`, `dut.rst_n`, `dut.ena`, `dut.ui_in`, `dut.uio_in`, `dut.uo_out`, `dut.uio_out`, `dut.uio_oe`. Drive inputs with `dut.ui_in.value = 1`. Read outputs with `int(dut.uo_out.value)`. A value with unknown (`x`) or high-impedance (`z`) bits cannot be converted by `int()`; that raises an error, which is often the first sign of a missing reset.

## Clock and reset

Start the clock once with `cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())`. Set `ena` to 1 and every input to a known value, hold `rst_n` at 0 for a few cycles, then set it to 1. Reset is active low and synchronous, so it only takes effect on a clock edge: hold it for at least two or three edges.

## Waiting and sampling

- `await ClockCycles(dut.clk, n)` waits for n rising edges.
- `await RisingEdge(dut.clk)` and `await FallingEdge(dut.clk)` wait for one edge.
- `await ReadOnly()` waits until the signals have settled in the current time step; you may read but not write until time moves on. Use `RisingEdge` then `ReadOnly` to read a register's value after the edge.
- Change inputs on the falling edge and read after the rising edge. Then the result does not depend on how a simulator orders events at the rising edge, and Icarus and Verilator agree.

## Writing assertions

Write `assert actual == expected`, with the measured value on the left and the expected value on the right. The server reports the left side as the actual value and the right side as the expected one, so keep that order. Add a message that says what was measured: `assert actual == expected, "count after 10 enabled clocks"`.

## Requirements and coverage

Every test has a docstring with a line `Covers: R1` (or `Covers: R1, R3`), using the ids in `spec.yaml`. Every requirement needs at least one test. A test that is skipped or marked as an expected failure does not count as coverage. Never assume a reset value that the spec does not state: sample the value after reset and check the change from it. Helper code may live in other files under `test/`, for example `test/helpers.py`; put shared code there and import it from your test file. Only `test/test*.py` files are collected as tests.

## Example: the counter design

This is the design under test: a counter that adds 1 on every clock while `ui_in[0]` is 1 and holds when it is 0. It is a Verilog counter; the Amaranth and MyHDL counters in their skills are checked with the test file below.

```verilog example=cocotb-idioms project=counter frontend=verilog file=src/counter.v
`default_nettype none

module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  reg [7:0] count;

  always @(posedge clk) begin
    if (!rst_n) count <= 8'd0;
    else if (ui_in[0]) count <= count + 8'd1;
  end

  assign uo_out = count;

  wire _unused = &{ui_in[7:1], 1'b0};

endmodule
```

## Example: the tests

Three tests, one per requirement. `sample` reads the output after the edge has settled, and each test measures the starting value instead of assuming one.

```python example=cocotb-idioms project=counter frontend=verilog file=test/test.py
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge


async def reset(dut):
    """Start a 10 ns clock and hold the active-low reset for 5 cycles."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 5)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def sample(dut):
    """Wait for a rising edge, let the outputs settle, and read uo_out."""
    await RisingEdge(dut.clk)
    await ReadOnly()
    return int(dut.uo_out.value)


@cocotb.test()
async def test_counts_when_enabled(dut):
    """Covers: R1"""
    await reset(dut)
    start = await sample(dut)  # the spec states no reset value, so measure it
    await FallingEdge(dut.clk)
    dut.ui_in.value = 1
    for _ in range(10):
        actual = await sample(dut)
    expected = (start + 10) % 256
    assert actual == expected, "uo_out after 10 enabled clocks"


@cocotb.test()
async def test_holds_when_disabled(dut):
    """Covers: R2"""
    await reset(dut)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 1
    for _ in range(7):
        await sample(dut)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 0
    expected = await sample(dut)
    await ClockCycles(dut.clk, 20)
    await ReadOnly()
    actual = int(dut.uo_out.value)
    assert actual == expected, "uo_out after 20 clocks with the count disabled"


@cocotb.test()
async def test_wraps_to_zero(dut):
    """Covers: R3"""
    await reset(dut)
    await FallingEdge(dut.clk)
    dut.ui_in.value = 1
    for _ in range(300):
        if await sample(dut) == 255:
            break
    else:
        raise AssertionError("the count never reached 255")
    actual = await sample(dut)
    assert actual == 0, "uo_out one clock after 255"
```

## Old cocotb 1.x code that no longer works

Most examples online are for cocotb 1.x. These idioms are gone in 2.x:

| Old (1.x) | Use instead (2.x) |
|---|---|
| `from cocotb.binary import BinaryValue` and `BinaryValue(...)` | A plain `int` to write; `int(dut.x.value)` to read. For bit-level work, `LogicArray` from `cocotb.types`. |
| `dut.x.value.integer` | `int(dut.x.value)` |
| `dut.x.value.signed_integer` | `dut.x.value.to_signed()` |
| `cocotb.fork(coro)` | `cocotb.start_soon(coro)` |
| `Clock(dut.clk, 10, units="ns")` and `Timer(5, units="ns")` | `unit="ns"` (singular) |
| `from cocotb.runner import get_runner` | `from cocotb_tools.runner import get_runner` (this server runs the simulators for you; you rarely need it) |
| `@cocotb.coroutine` and `yield trigger` | `async def` and `await trigger` |
| `raise TestFailure("...")` (`cocotb.result.TestFailure`) | `assert` or `raise AssertionError("...")` |
| `dut.x <= 1` | `dut.x.value = 1` |

When a test fails with an `ImportError` or `AttributeError` from the cocotb package, look at this table first.
