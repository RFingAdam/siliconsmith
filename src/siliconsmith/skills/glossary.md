# Glossary

Plain-English meanings of the terms that Siliconsmith and Tiny Tapeout use, with RF comparisons where they help. Each entry is a `##` heading so that `explain('<term>')` can find it.

## RTL

Register-transfer level: a description of a chip as registers (memory that updates on a clock edge) and the logic between them. It is the level at which you write a design. Think of a block diagram where every box is either a register or some gates.

## HDL

Hardware description language: a language for writing RTL. Verilog is the classic one; Amaranth and MyHDL are Python libraries that generate Verilog. An HDL describes hardware that all runs at once, not a program that runs line by line.

## Testbench

Code that drives a design's inputs and checks its outputs in simulation. It is the signal generator plus the spectrum analyzer of a chip.

## cocotb

A Python library for writing testbenches. Your tests are Python functions that set pins, wait for clock edges and assert on outputs. Siliconsmith uses cocotb 2.1.

## Simulation

Running the design as software to see what it would do. Time moves in small steps and every signal has a value at each step. It checks the logic, not the physics: no real delays, noise or temperature.

## Verilog

The classic hardware description language, and the file format every tool in the chain reads. Amaranth and MyHDL designs are converted to Verilog by `emit_verilog`, and the simulators and later the synthesis step work on that Verilog. A `.v` file describes hardware that all runs at once.

## Icarus Verilog

An open-source Verilog simulator (iverilog). Slow on big designs, forgiving, and a good first check.

## Verilator

An open-source simulator that turns the design into C++ and compiles it. It is fast and strict, and it also acts as a linter. Siliconsmith runs your tests on both Icarus and Verilator, because they catch different mistakes.

## Lint

An automatic check of the source for likely mistakes without running it: width mismatches, signals nobody drives, latches. Like checking a schematic for floating pins before you simulate.

## Latch

A memory element made by accident when combinational logic does not give a signal a value on every path. It is level-sensitive instead of clocked, which makes timing hard to analyze. Tiny Tapeout designs should have none, and the lint step treats them as errors. A latch you write on purpose (`always_latch` in Verilog) is allowed: synthesis maps it to a latch cell and warns, naming the file and line.

## Combinational logic

Logic whose output depends only on its inputs right now, with no memory, like a passive network or a mixer: change the input and the output follows. In Amaranth it is `m.d.comb`; in MyHDL it is `@always_comb`. Combinational logic that forgets to give a value on some path can turn into a latch.

## Register

Also called a flip-flop: a one-bit memory that copies its input on a clock edge and holds it until the next one. It is the sample-and-hold of a digital design, and a counter or a state machine is built from registers. Logic written with `m.d.sync` or `@always_seq` becomes registers.

## Multiple drivers

Two pieces of logic driving the same wire. Real hardware would have a fight between them (like two outputs tied together). Every signal gets exactly one driver.

## Clock domain

The set of registers that share one clock. Passing a signal from one clock domain to another needs special care, like resampling a signal at a different sampling rate. Siliconsmith designs use a single clock domain.

## Reset

A signal that puts all registers into a known starting state. Tiny Tapeout's `rst_n` is active low (0 means reset) and synchronous (it takes effect on a clock edge). Without a reset, registers start with unknown values.

## Wrapper

The small generated module `tt_um_<name>` that gives your core the fixed Tiny Tapeout pin list. You write the core; the tool writes the wrapper and you never edit it.

## Synthesis

Turning RTL into a netlist of standard cells (a gate-level circuit). It is the step where the design becomes real gates: a program (Yosys) picks gates and flip-flops from the chip factory's library that do what your RTL says, trying to keep them small. Siliconsmith's `synthesize` tool does it with the same recipe as Tiny Tapeout's own flow, and reports the cell count, the area and the estimated utilization.

## Netlist

A list of gates and the wires between them, the output of synthesis. Like a schematic as a text file. `synthesize` saves it in `build/synth/` (the copy from the latest run that produced one).

## Gate-level simulation

Simulating the netlist instead of the RTL, using the gate models of the chip library, to check that synthesis did not change the behavior. In Siliconsmith it is `run_tests` with `gl=true`: the same cocotb tests, on the gates. It catches missing resets, unknown (X) values and other places where the gates and the RTL disagree. It does not check real timing: see unit delay.

## X (unknown value)

A signal value that is neither 0 nor 1 in a simulation, written `x`. A flip-flop that has not been reset starts as `x`, and anything computed from an `x` is usually `x` too. RTL simulation can hide it (a register may start at 0); gate-level simulation shows it. `z` is a different value: nothing drives the wire.

## Unit delay

The fixed 1 ns delay that the gate models (`UNIT_DELAY=#1`) put on every flip-flop output in gate-level simulation. It keeps the simulation in a sensible order, but it is not real timing: the real delays of the cells and the wires come from layout and static timing analysis, in a later milestone.

## Standard cell

A small pre-designed building block (a NAND gate, a flip-flop, an inverter) with a fixed height, from the chip factory's library. Synthesis builds your design out of these. Think of the parts bin of a very small gate library: every cell has a known area and delay.

## Utilization

How full the chip area is: the area of all the cells divided by the area the placer may fill. Before layout it can only be estimated, and `synthesize` says so. Under about 90% is comfortable, above 100% the design does not fit the tile.

## Do-not-use cell

A library cell that Tiny Tapeout's flow keeps out of designs (it fails the factory's drawing rules, has awkward pins, or is a low-power `lpflow_` cell). The synthesis tool is told not to use them, and `synthesize` fails if one shows up in the netlist.

## PDK

Process design kit: the files that describe one chip process: the rules, the cell library and the device models. Tiny Tapeout uses sky130, an open PDK for SkyWater's 130 nm process.

## sky130

The open SkyWater 130 nm process and its PDK. Tiny Tapeout's current digital shuttles use it.

## Place and route

Placing every standard cell on the chip area and drawing the wires between them. The step that turns a netlist into a physical layout. Not available in this milestone.

## GDS

The file format (GDSII) for a chip's layout: the polygons for every layer that the factory makes masks from. It is the file that is actually manufactured.

## DRC

Design rule check: a check that the layout obeys the factory's drawing rules (minimum widths and spacings). A layout that fails cannot be made.

## LVS

Layout versus schematic: a check that the layout's devices and connections match the netlist it came from. It catches a wire that is missing or shorted in the layout.

## Antenna

A rule violation in which a long piece of metal collects charge during manufacturing and can damage a transistor gate. Fixed by adding diodes or changing the routing. Nothing to do with radio.

## Slack

The spare time on a path: the clock period minus the time the signal needs to get through the logic. Positive is fine, negative is a timing failure. Like link margin in a link budget.

## Setup and hold violation

Setup: data must arrive a short time before the clock edge. Hold: data must stay steady for a short time after it. Breaking either can make a register capture the wrong value. Think of the sample-and-hold timing of an ADC.

## Timing closure

Getting every path to meet its setup and hold requirements at the chosen clock speed, usually by changing the design or the layout and checking again.

## Tile

The unit of area on a Tiny Tapeout chip. A 1x1 tile is 161.00 x 111.52 um, roughly a thousand gates. Bigger designs buy more tiles, and each extra tile costs money. The `tiles` field in `spec.yaml` says how many (default `1x1`).

## Shuttle

A shared manufacturing run where many small designs are put on one chip to share the cost. Names such as SKY26e mean sky130, year 2026, run e. A shuttle has a closing date, and the owner picks one and submits by hand.

## Tiny Tapeout

A low-cost program that puts small designs from many people on a shared chip in a shuttle run. Each design gets a fixed set of pins and a tile or more of area. Siliconsmith prepares a project for it but never submits or pays.

## MCP server

The small program that your AI agent starts and talks to. MCP (Model Context Protocol) is the standard way an agent calls tools. Siliconsmith is an MCP server: its tools create projects, write files and run the checks.

## Docker image

A packaged, frozen copy of the chip tools (simulators, synthesis and so on) that runs in a container, so every machine uses the same versions. Siliconsmith runs all design code and tools inside one pinned Docker image; you need Docker installed, not the individual tools.

## Waveform

A recording of how signals changed over time in a simulation, saved as a VCD or FST file. The oscilloscope trace of a simulation. `run_tests(waves=True)` keeps one and `inspect_waves` reads it.

## Spec

The short file `spec.yaml`: the clock, the pin meanings and the numbered requirements. It is the one place that says what the chip should do, and the owner reads and approves it.

## Requirement coverage

Whether each numbered requirement in the spec has at least one passing test that says it covers it (`Covers: R1`). Full coverage means every requirement is tested, not that the tests are good enough.
