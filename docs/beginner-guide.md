# Beginner guide

This guide is for someone who knows RF and analog hardware well and is new to digital chip design. It explains what Siliconsmith does, what each check proves (and what it does not), and what you do yourself. Words in **bold** are in the [glossary](glossary.md).

## What Siliconsmith is

Siliconsmith is a small program (an MCP server) that your AI coding agent starts and talks to. It gives the agent tools to create a chip project, write a spec, write tests, write the design, and run checks on it. All the open-source chip tools run inside one pinned Docker image, so you only need Docker and Python on your machine.

The target is [Tiny Tapeout](https://tinytapeout.com/): a program that puts many small digital designs on one shared chip. Today Siliconsmith takes a design as far as passing simulation, turning into real gates (synthesis), simulating those gates and proving they match the RTL (equivalence). Layout and the export to a submission repo come in later milestones.

## The loop in plain English

1. **Spec.** The agent writes `spec.yaml`: the clock speed, what each pin does, and numbered requirements (R1, R2, ...) in your words. This is the contract. You read it.
2. **Tests first.** The agent writes tests (a **testbench** in **cocotb**, which is Python) before the design. Each test says which requirement it covers. This is like writing the measurement plan before building the circuit.
3. **Design.** The agent writes the design (**RTL**) in Amaranth or MyHDL (both Python) or in Verilog.
4. **Emit and lint.** The tool turns the design into Verilog and **lints** it: it checks pin names, widths, **latches** and **multiple drivers**.
5. **Simulate twice.** The tests run on two **simulators**, Icarus and Verilator. They catch different mistakes, so both must pass.
6. **Synthesize.** The tool turns the design into real **standard cells** and reports how big it is. See "From RTL to gates" below.
7. **Simulate the gates, then check equivalence.** The agent runs your tests on the real gates, then asks a proof program whether the gates do the same as the RTL.
8. **Status.** The `status` tool shows which checks pass for the current files and writes `docs/verification.md`.

If a test fails, the agent gets the failing assertion with the expected and actual values, and can look at a **waveform** (the simulation's oscilloscope trace).

You can ask the agent for an explanation at any time: the `explain` tool answers from the built-in skills and glossary (try asking it to `explain('chip-design-loop')` or `explain('latch')`).

## What each check proves, and what it does not

| Step | What it proves | What it does not prove |
|---|---|---|
| Spec | The requirements are written down in one place. | That they are what you meant, until you read them. |
| Emit and lint | The design converts to Verilog, uses exactly the Tiny Tapeout pins, and has no latches, multiple drivers or logic loops. | That the design does the right thing. |
| Simulation on Icarus and Verilator | Under the tests, the design behaves as the tests say, on two independent simulators. | Anything the tests do not check. Simulation has no real delays, noise or temperature. |
| Synthesis | The design turns into real sky130 gates with no combinational loop, no signal driven twice, no undriven output and no forbidden cell, and you learn its size (cells, area, estimated **utilization**). | That the gates behave like the RTL (gate-level simulation and the equivalence check show that), that the design meets timing, or the real size: utilization is an estimate until layout. |
| Gate-level simulation | Your same tests, run on the real sky130 gates from synthesis, pass. This catches a register with no reset (it starts unknown on real gates) and other simulation-versus-gates differences. | Real timing: the gates use a fixed 1 ns unit delay. Anything the tests do not check. |
| Equivalence | A proof program (Yosys) compares the real gates with your RTL: every output value the RTL defines matches for every power-up state of the flip-flops. A difference is named by output bit and clock cycle. "Not proven" (for example latches, a falling-edge clock, or the proof running out of time) is not a pass. | Bugs in the RTL itself: a wrong or missing reset in the RTL is the same on both sides. Reset and power-up behaviour (gate-level simulation checks that) and timing. |
| Requirement coverage | Every requirement has at least one passing test that says it covers it. | That the tests are strong enough. A weak test still counts. |
| Spec approval | You read the spec and accepted it. This is your step. | Nothing technical. |

Not available yet: **place and route**, **DRC**, **LVS**, **timing closure** (**slack**, **setup and hold**), and the Tiny Tapeout export. A design that passes today's checks is a good simulated design, not working silicon.

## From RTL to gates

Your RTL says what the chip does. **Synthesis** turns it into a **netlist**: a list of real **standard cells** (NAND gates, flip-flops, inverters from the SkyWater sky130 library) and the wires between them. It is a bit like going from a block diagram to a parts list for a small gate library, where every part has a known area.

The `synthesize` tool does this with Yosys, using the same recipe as Tiny Tapeout's own flow, at the clock speed from your `spec.yaml`. Look at what it tells you:

- **Cells**: how many parts there are, split into **flip-flops** (the registers that remember a bit) and **logic** (everything else). A cell count of a few dozen for a small counter is normal. Every unused output pin costs one tiny tie cell, so you will see a few "conb" cells: that is fine.
- **Area** in um2: the sum of the parts' areas.
- **Utilization**: how full your tile would be, about the cell area divided by the area the placer may fill. A 1x1 tile holds about 9,900 um2 of cells. Under 90% is comfortable. It is an **estimate**: the real number comes from layout, in a later milestone.
- **Warnings and problems**: an accidental **latch** or a **combinational loop** is reported with the file and line (for MyHDL, with your `hdl/` function too). A latch you wrote on purpose is allowed and shown as a warning. Cells that Tiny Tapeout's flow does not allow (**do-not-use cells**) are rejected.
- **Too big**: if the design is far larger than the tile, the tool says so quickly, without doing the slow part. Make the design smaller, or ask the owner (you) whether a bigger tile is worth the money. The agent never changes the tile itself.

### Simulating the gates

`run_tests` with `gl=true` (on Icarus) synthesizes the design again and then runs your same test files on the netlist, with a model of each sky130 cell. It is the closest thing to the real chip that a simulator can show before layout.

The classic thing it catches is a register without a reset. In RTL simulation a register can start at 0 because the simulator (or an `initial` line) says so. A real flip-flop powers up as an unknown 0 or 1, and gate-level simulation shows that as **X** (unknown). When your tests fail on the gates, the report says "First difference on the pins": which output, at which clock cycle, what the RTL had and what the gates had, for example `RTL 00000000, gate level xxxxxxxx`. The fix is almost always a proper reset. The waves of the gate-level run are in `build/waves/icarus-gl/tb.fst`.

Two limits to remember. The tests can only look at the chip's pins, because inside a netlist the names are changed (`\core.count[0]`) or gone. And the delay is a fixed 1 ns on every flip-flop (the **unit delay**), not the real timing of the cells and wires: timing comes with layout.

Clocks above about 66 MHz get a warning, because Tiny Tapeout's pins cannot move data that fast. Synthesis does not check timing; that comes with layout.

### Proving the gates match

`check_equivalence` is a different kind of check: not a test, but a **formal proof**. It synthesizes the design again and a proof program (Yosys) compares the gates with your RTL. Where a test tries some inputs, the proof covers all of them. Think of it as checking a layout against its schematic rather than measuring the board. It takes about ten seconds for a counter, and a minute or more for memories or wide multipliers.

There are three answers:

- **Equivalent**: synthesis did not change your logic: every output value the RTL defines matches the gates, for every power-up state of the flip-flops. Where the RTL says x (unknown, do not care) the gates may do anything. It does catch a register in the gates that reset never sets, and RTL that only works because of an `initial` value on a register that reset does not set. If that value shows on an output within 32 cycles of reset, the tool says `not_equivalent` (`rtl_initial_value`); if it cannot see an effect that early, it says not proven (`initial_values`) rather than a pass. Either way it tells you to reset those registers.
  It cannot see everything a simulator does: an out-of-range index or a division by zero (x in a simulator, some value in the gates) and anything that depends on the order a simulator runs events. Gate-level simulation (`run_tests` with `gl=true`) is the check against simulation behaviour, so run both.
- **Not equivalent**: a **counterexample**: the output bit, the clock cycle and the two values (RTL against gates). x means the value depends on how the flip-flop powered up. Fix the cause and run it again.
- **Not proven**: the proof could not be finished. Latches, a falling-edge or derived clock, tri-state outputs, flip-flops with both an asynchronous set and reset, RTL with constructs a simulator and the synthesis read differently (macros, `#` delays, `bit`, `unique`, `include`, a level list like `always @(a or b)`: write `always @(*)`; in MyHDL a level-sensitive block must list every signal it reads, which `@always_comb` always does, so use it), and proofs that run out of time all end here. It is **not** a pass, and the agent must say so.

Two things it does not do. It cannot find a bug that is in the RTL itself, such as a wrong reset value: both sides have the same bug, so they agree. (Your tests must catch those.) And it takes the reset to be one clock cycle, comparing from cycle 2. Gate-level simulation stays the check for reset and power-up behaviour, and neither one says anything about timing.

## Add the server to Claude Code

Install it once (the README has the details), then put this in a `.mcp.json` file at the top of your project folder:

```json
{
  "mcpServers": {
    "siliconsmith": {
      "command": "siliconsmith-mcp",
      "args": []
    }
  }
}
```

Or run `claude mcp add --scope project siliconsmith -- siliconsmith-mcp`. In Claude Code, run `/mcp` to check that `siliconsmith` is connected, then ask for a chip, for example: "Use Siliconsmith to design an 8-bit counter on uo_out with the count enable on ui_in[0]." You need Docker and the toolchain image (`toolchain_info` checks both).

## Reading `docs/verification.md`

`docs/verification.md` is written by the `status` tool (or by `siliconsmith status <project folder>` in a terminal). It is a table with one row per step: what the step proves in plain English, its state, the evidence and the date. The states are:

- **pass**: the step ran on the current files and passed.
- **fail**: it ran and failed.
- **stale**: it ran before (pass or fail), but a file it depends on has changed since. Run it again.
- **not run**: it has never run.
- **not available**: the step belongs to a later milestone.

Siliconsmith's own tools cannot write this file; `status` rebuilds it from the recorded runs. Like approval, this relies on honest use: an agent with its own shell could change files.

## Approving the spec

The spec approval is yours. When you have read `spec.yaml` and agree, run `siliconsmith approve <project folder>` in a terminal. It asks you to confirm. This is a convention based on honest use, not a lock: an agent that has its own shell could run the command, and the skills tell it not to. The approval records the spec's hash, so it applies to that version of the spec.

## How a Tiny Tapeout submission works (later)

When the later milestones land, Siliconsmith will produce a repository that is ready for Tiny Tapeout. You then submit it, by hand, on the Tiny Tapeout website, pick a **shuttle**, and pay. Siliconsmith never orders or pays for anything. Prices, closing dates and slots change, so check [tinytapeout.com](https://tinytapeout.com/) when you decide. A digital **tile** is small (about a thousand gates), so start with something modest, such as a counter, a state machine or a shift register.

Two honest limits: a working 2.4 GHz radio, such as a Bluetooth transceiver, is not realistic on any Tiny Tapeout tile, digital or analog (the sky130 RF models are limited, the pads and package are not made for GHz signals, and the area is tiny); small analog blocks such as an oscillator, VCO or LNA are the realistic targets of the later analog track. SAW and BAW filters have no open fabrication path in this project.

## Where to look next

- [Glossary](glossary.md): every term used here, with RF comparisons.
- The skills the agent reads: `chip-design-loop`, `tt-rules`, `amaranth-style`, `myhdl-style` and `cocotb2-tests`, in `src/siliconsmith/skills/`.
- [README](../README.md): install and agent setup.
