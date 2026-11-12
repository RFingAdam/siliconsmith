---
name: chip-design-loop
description: Use this first, whenever you are asked to design a small digital chip (or any block of one) with the Siliconsmith tools.
---

# The chip design loop

The loop is: spec, then tests, then the design, then check and simulate on two simulators, then report. It uses only this server's tools. A passing run means the design does what the tests say in simulation. It does not mean working silicon.

Rules that always apply:

- Write the spec and the tests before the design. Every requirement needs a test with `Covers: R<n>`.
- Read `explain('cocotb2-tests')` before writing tests and the style skill for your front-end (`amaranth-style`, `myhdl-style`) before the design.
- Never edit a test to make a wrong design pass, and never weaken a requirement silently.
- Stop after 8 `run_tests` tries per simulator on the same failure and report. If a requirement is unclear, ask the user; in a headless run, write your assumption to `open_questions` with `write_spec` and continue.
- Approving the spec is the owner's step, never yours.
- Use only this server's tools. Passing simulation is not working silicon.

## The steps

1. `create_project` once (name first, then every other tool takes that name). Pick the front-end the user asked for: amaranth, myhdl or verilog.
2. `write_spec`: write `spec.yaml` first. It holds `clock_hz`, the optional `tiles` (leave it at `"1x1"`; a bigger tile costs the owner money, so ask first), the pin meanings and numbered requirements R1, R2, ... taken from the user's own words. Replace the placeholder spec.
3. `write_test`: write the cocotb tests before the design. Every test docstring says `Covers: R1` (or `Covers: R1, R2`). Every requirement needs at least one test that covers it.
4. `write_rtl`: write the design (the core module named after the project). Whole files only, never diffs.
5. `emit_verilog`: turns the design into Verilog and lints it. Fix every lint finding before simulating. A latch or a multiply driven signal is an error.
6. `run_tests` with `simulator="icarus"`, then again with `simulator="verilator"`. Both must pass; they disagree sometimes, and that is a real finding.
7. `status`: shows which checks pass for the current files and writes `docs/verification.md` for the owner to read.

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

`status` shows the check, both simulations and requirement coverage as pass. The spec approval shows as the owner's step, still open. Later checks (synthesis, gate-level simulation, layout, Tiny Tapeout export) are not available yet; say so rather than implying the chip is ready to submit. Submitting to a shuttle is always done by the owner by hand.
