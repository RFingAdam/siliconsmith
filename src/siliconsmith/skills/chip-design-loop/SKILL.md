---
name: chip-design-loop
description: Use this first, whenever you are asked to design a small digital chip (or any block of one) with the Siliconsmith tools.
---

# The chip design loop

The loop is: spec, then tests, then the design, then check and simulate on two simulators, then synthesize into real gates, then simulate those gates, then prove the gates match the RTL, then report. It uses only this server's tools. A passing run means the design does what the tests say in simulation. It does not mean working silicon.

Rules that always apply:

- Write the spec and the tests before the design. Every requirement needs a test with `Covers: R<n>`.
- Read `explain('cocotb2-tests')` before writing tests and the style skill for your front-end (`amaranth-style`, `myhdl-style`) before the design.
- Never edit a test to make a wrong design pass, and never weaken a requirement silently.
- Stop after 8 `run_tests` tries per simulator on the same failure and report. If a requirement is unclear, ask the user; in a headless run, write your assumption to `open_questions` with `write_spec` and continue.
- Approving the spec is the owner's step, never yours.
- Use only this server's tools. Passing simulation is not working silicon.

## The steps

1. `create_project` once (name first, then every other tool takes that name). Pick the front-end the user asked for: amaranth, myhdl or verilog.
2. `write_spec`: write `spec.yaml` first. It holds `clock_hz`, the optional `tiles` (leave it at `"1x1"`; a bigger tile costs the owner money, so ask first), the pin meanings and numbered requirements R1, R2, ... from the user's own words. Replace the placeholder spec.
3. `write_test`: write the cocotb tests before the design. Every test docstring says `Covers: R1` (or `Covers: R1, R2`). Every requirement needs at least one test that covers it.
4. `write_rtl`: write the design (the core module named after the project). Whole files only, never diffs.
5. `emit_verilog`: turns the design into Verilog and lints it. Fix every lint finding before simulating. A latch or a multiply driven signal is an error.
6. `run_tests` with `simulator="icarus"`, then again with `simulator="verilator"`. Both must pass; they disagree sometimes, and that is a real finding.
7. `synthesize`, once both simulators pass: it builds real sky130 gates and reports cells, area and tile fill (see "What synthesis proves").
8. `run_tests(simulator="icarus", gl=true)`: your same tests on the real gates (see below). It synthesizes by itself.
9. `check_equivalence`: proves the gates do the same as your RTL (see "What equivalence proves").
10. `status`: shows which checks pass for the current files and writes `docs/verification.md` for the owner to read.

## What synthesis proves

`synthesize` runs the same checks as `emit_verilog` (a lint problem stops it, with file and line), then Yosys builds the design out of sky130 standard cells, the way Tiny Tapeout's own flow does.

A pass means: the design turns into gates with no combinational loop, no signal driven twice, no undriven output, and no cell that Tiny Tapeout's flow forbids. It also tells you how many cells (flip-flops and logic) and how many um2 the design needs, and the utilization: how much of the tile's usable area those cells would fill.

Utilization is an estimate. Layout (a later milestone) gives the real number. Above 90% placement may struggle; above 100% the design will not fit. It does not prove the gates behave like your RTL (gate-level simulation and the equivalence check do that) and it says nothing about timing yet.

A latch you wrote on purpose (`always_latch`) passes with a warning. An accidental latch (an incomplete `if` in combinational logic) is a lint error with file and line; for MyHDL the message also names your `hdl/` function and line. Call `synthesize` again after every design change; `status` shows Synthesis as stale when the design, the clock or the tile changed.

## What gate-level simulation proves

`run_tests(simulator="icarus", gl=true)` runs your same tests, unchanged, on the real sky130 gates that synthesis made. Its summary starts with "Gate-level simulation"; it never changes the RTL results or waves (its own waves are in `build/waves/icarus-gl/`).

A pass means the gates behave as the tests say. This is what catches reset and initial-value bugs (a register that only works because the simulator started it at 0, while a real flip-flop powers up unknown), X (unknown) values that reach the outputs, and any other place where the gates and the RTL simulation disagree.

It does not prove timing: the cell models use a fixed 1 ns unit delay, with no real gate delays, wire delays or clock-to-output times. Timing comes with layout in a later milestone. Nor does it prove the design is right where the tests say nothing.

When a gate-level test fails, the summary says "First difference on the pins": the signal, the clock cycle, and the RTL value against the gate-level value (x means unknown). Look at `build/waves/icarus-gl/tb.fst` with `inspect_waves`. In a gate-level run the tests can only touch the `tb` pins (`explain('cocotb2-tests')`).

An x on a pin has two usual causes: an input the test never drove (`ui_in`, `uio_in`, `ena`): the RTL treats an unknown `if` as false, the gates do not. Or a register with no reset, or one that relies on an `initial` value, which a real chip ignores: use the reset. The summary names an undriven input.

## What equivalence proves

`check_equivalence` synthesizes again by itself, then a proof program (Yosys) compares the gates with your RTL. A small design takes about 10 seconds, big ones a minute or more. Read the first line of the summary, then run `status`.

"Equivalent" means: every output value the RTL defines matches the gates, for every power-up state of the flip-flops. Where the RTL says x (do not care) the gates may do anything. Registers without a reset are only compared for a consistent power-up. It catches a register in the gates that reset never sets.

A register that keeps an initial value (`reg x = 0`, `initial`, Amaranth `init=`) which reset does not set comes back `not_equivalent` (`rtl_initial_value`) or not proven (`initial_values`): reset it with `rst_n`.

It does not catch bugs in the RTL itself, including a wrong reset in the RTL: both sides share them, so only your tests can. Reset is 1 cycle, compared from cycle 2. Gate-level simulation is still the check for reset and power-up behaviour.

`not_equivalent` names the output bit and the first differing clock cycle (x means it depends on power-up). Fix and rerun.

`not_proven` is never a pass. Latches, derived or falling-edge clocks, z (tri-state), flops with async set and reset are unsupported and come back not proven, as does a proof that runs out of time. Tell the user it is not checked. An `error` (a loop, an unknown cell) is no pass either.

## What equivalence does not see

"Equivalent" means synthesis did not change your logic: the gates match the RTL as Yosys reads it. A simulator reads some constructs differently, so the check refuses the common ones as not proven (`guard_sim_synth_differ`) and names them: macros and `ifdef`, `include`, `bit`/`int`, `unique`/`priority`, `casex`, `===`, x or z literals, `#` delays and a `lint_off` of a race warning.

Write `always @(*)` (a list of level signals is refused) and `function automatic`.
Write every signal of a combinational block before you read it (a simulator does not rerun the block for its own write; `y = t; t = cnt;` is refused, in a loop too).
A `function` may read only its arguments (pass the rest in) and a `task` is refused.
The check cannot see, in hand-written Verilog, a read and a write that happen in that order only across loop iterations (Verilator cannot see it); gate-level simulation is the check for that.
In MyHDL the core must be the plain single module MyHDL writes, and a level-sensitive block must have the plain shape `@always_comb` writes and list every signal it reads; otherwise it is refused, so use `@always_comb`.
Use a parameter, not a macro.

It cannot see an out-of-range index or a division by zero (x in a simulator, some value in the gates), a localparam that Yosys reads differently from a simulator, or anything that depends on the order a simulator runs events. Gate-level simulation (`run_tests` with `gl=true`) is the check against simulation behaviour: run both.

## Too big for the tile

If `synthesize` says "Too big for 1x1", make the design smaller first: fewer bits, no wide multipliers or big arrays, one shared piece of logic over several clocks. The tool stops a design that is far too big before the slow step, so you hear quickly. Only if the design really needs more room, ask the owner about a bigger tile in `spec.yaml` (`tiles`): bigger tiles cost money, so it is the owner's decision.

In a headless run, write the assumption to `open_questions` with `write_spec`; never change `tiles` yourself.

The clock is checked too: above about 66 MHz Tiny Tapeout's pins cannot keep up, and `synthesize` warns.

## Writing the spec

Keep each requirement testable: one sentence that a test can check with a number or a yes or no. "The count goes up by one on every clock while ui_in[0] is 1" is testable. "The counter is fast" is not.

Use the user's words. Do not add requirements the user did not ask for, and do not drop one silently. State anything you had to assume in `open_questions`. The clock is `clock_hz` in `spec.yaml`; it is the only place it is written. See `explain('tt-rules')` for the pin interface.

## Writing tests first

Tests are the contract. Write them from the requirements, not from your design. Do not assume a reset value that the spec does not state: read the value after reset and check changes relative to it. See `explain('cocotb2-tests')` for idioms that work, and `explain('amaranth-style')` or `explain('myhdl-style')` for the design side.

## When something fails

Read the first failing test in the `run_tests` summary: it gives the file, the line, the assertion, and the expected and actual values. If the cause is not clear, rerun that test with `run_tests(waves=True, test_filter=...)` and look at signals with `inspect_waves`. Fix the design. A filtered rerun, or one with waves, is for diagnosis only: finish with full runs (no `test_filter`) on both simulators, because only those count in `status`.

Never edit a test to make a wrong design pass. Edit a test only when the test itself is wrong, and say why. Never weaken a requirement silently; change it in `write_spec` and tell the user.

## Stop rules

- At most 8 `run_tests` iterations per simulator on the same failure. Then stop and report what you tried and what you saw.
- If a requirement is unclear, stop and ask the user.
- In a headless run with nobody to ask, write your assumptions to `open_questions` with `write_spec` and continue.
- Use only the tools this server lists. Do not run other tools on the host to check or build the design.

## Approval is the owner's step

The owner approves the spec, from a terminal, with `siliconsmith approve`. It is an honest-use convention, not a security boundary, and you must never run it yourself. Tell the user that approval is their step.

## What done looks like

`status` shows the check, both simulations, requirement coverage, Synthesis, Gate-level simulation and Equivalence as pass. The spec approval shows as the owner's step, still open. Later checks (layout, Tiny Tapeout export) are not available yet; say so rather than implying the chip is ready to submit. Submitting to a shuttle is always done by the owner by hand.
