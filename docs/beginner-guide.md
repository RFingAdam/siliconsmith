# Beginner guide

This guide is for someone who knows RF and analog hardware well and is new to digital chip design. It explains what Siliconsmith does, what each check proves (and what it does not), and what you do yourself. Words in **bold** are in the [glossary](glossary.md).

## What Siliconsmith is

Siliconsmith is a small program (an MCP server) that your AI coding agent starts and talks to. It gives the agent tools to create a chip project, write a spec, write tests, write the design, and run checks on it. All the open-source chip tools run inside one pinned Docker image, so you only need Docker and Python on your machine.

The target is [Tiny Tapeout](https://tinytapeout.com/): a program that puts many small digital designs on one shared chip. Today Siliconsmith takes a design as far as passing simulation. Synthesis, layout and the export to a submission repo come in later milestones.

## The loop in plain English

1. **Spec.** The agent writes `spec.yaml`: the clock speed, what each pin does, and numbered requirements (R1, R2, ...) in your words. This is the contract. You read it.
2. **Tests first.** The agent writes tests (a **testbench** in **cocotb**, which is Python) before the design. Each test says which requirement it covers. This is like writing the measurement plan before building the circuit.
3. **Design.** The agent writes the design (**RTL**) in Amaranth or MyHDL (both Python) or in Verilog.
4. **Emit and lint.** The tool turns the design into Verilog and **lints** it: it checks pin names, widths, **latches** and **multiple drivers**.
5. **Simulate twice.** The tests run on two **simulators**, Icarus and Verilator. They catch different mistakes, so both must pass.
6. **Status.** The `status` tool shows which checks pass for the current files and writes `docs/verification.md`.

If a test fails, the agent gets the failing assertion with the expected and actual values, and can look at a **waveform** (the simulation's oscilloscope trace).

You can ask the agent for an explanation at any time: the `explain` tool answers from the built-in skills and glossary (try asking it to `explain('chip-design-loop')` or `explain('latch')`).

## What each check proves, and what it does not

| Step | What it proves | What it does not prove |
|---|---|---|
| Spec | The requirements are written down in one place. | That they are what you meant, until you read them. |
| Emit and lint | The design converts to Verilog, uses exactly the Tiny Tapeout pins, and has no latches, multiple drivers or logic loops. | That the design does the right thing. |
| Simulation on Icarus and Verilator | Under the tests, the design behaves as the tests say, on two independent simulators. | Anything the tests do not check. Simulation has no real delays, noise or temperature. |
| Requirement coverage | Every requirement has at least one passing test that says it covers it. | That the tests are strong enough. A weak test still counts. |
| Spec approval | You read the spec and accepted it. This is your step. | Nothing technical. |

Not available yet: **synthesis**, **gate-level simulation**, **place and route**, **DRC**, **LVS**, **timing closure** (**slack**, **setup and hold**), and the Tiny Tapeout export. A design that passes today's checks is a good simulated design, not working silicon.

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
