# Beginner guide

This guide is for someone who knows RF and analog hardware well and is new to digital chip design. It explains what Siliconsmith does, what each check proves (and what it does not), and what you do yourself. Words in **bold** are in the [glossary](glossary.md).

## What Siliconsmith is

Siliconsmith is a small program (an MCP server) that your AI coding agent starts and talks to. It gives the agent tools to create a chip project, write a spec, write tests, write the design, and run checks on it. All the open-source chip tools run inside one pinned Docker image, so you only need Docker and Python on your machine.

The target is [Tiny Tapeout](https://tinytapeout.com/): a program that puts many small digital designs on one shared chip. Today Siliconsmith takes a design as far as passing simulation, turning into real gates (synthesis), passing the same tests on those gates (gate-level simulation) and proving the gates match the RTL as Yosys reads it (equivalence). Layout (M2) and the export to a submission repo (M3) come next.

## The loop in plain English

1. **Spec.** The agent writes `spec.yaml`: the clock speed, what each pin does, and numbered requirements (R1, R2, ...) in your words. This is the contract. You read it.
2. **Tests first.** The agent writes tests (a **testbench** in **cocotb**, which is Python) before the design. Each test says which requirement it covers. This is like writing the measurement plan before building the circuit.
3. **Design.** The agent writes the design (**RTL**) in Amaranth or MyHDL (both Python) or in Verilog.
4. **Emit and lint.** The tool turns the design into Verilog and **lints** it: it checks pin names, widths, **latches** and **multiple drivers**.
5. **Simulate twice.** The tests run on two **simulators**, Icarus and Verilator. They catch different mistakes, so both must pass.
6. **Synthesize.** The tool turns the design into real **standard cells** and reports how big it is. See "From RTL to gates" below.
7. **Simulate the gates, then check equivalence.** The same tests run again on those cells (**gate-level simulation**), then a proof program checks that the gates do the same as the RTL. See "Simulating the gates" below.
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

- **Equivalent**: synthesis did not change your logic: every output value the RTL defines matches the gates, for every power-up state of the flip-flops (the proof tries every 0/1 start value of each gate flip-flop, exactly, not an approximation). Where the RTL says x (unknown, do not care) the gates may do anything. It does catch a register in the gates that reset never sets, and RTL that only works because of an `initial` value on a register that reset does not set. If that value shows on an output within 32 cycles of reset, the tool says `not_equivalent` (`rtl_initial_value`); if it cannot see an effect that early, it says not proven (`initial_values`) rather than a pass. Either way it tells you to reset those registers.
  It cannot see everything a simulator does: an index computed from a signal or a variable that goes outside the range (x in a simulator, which an `if` can turn into a 0 or 1; some value in the gates), also a loop index passed into a function that selects with it and an array index inside a function (Yosys warns only about a vector select), a division by a signal that is 0, a function that reads one of its own locals before it writes it, an inline call of a function that leaves its result unset for some constant arguments (x in a simulator), an `initial` value on a register that reset does not set when its next value in the reset cycle depends on another register's power-up value (that unknown hides it), a constant that Yosys works out differently from a simulator (a size cast or `'1` inside a `localparam`, `$rtoi` in any constant expression such as a `localparam` or a generate condition, a real parameter override, which Yosys passes to the instance with six decimals so `1.0e-9` becomes 0, a finite real mixed into a signal expression such as `ui_in == 2.5`, which Yosys rounds to an integer first, or a select outside the range inside a constant function, also in a parameter's value, which Yosys 0.62 works out to an undefined value, so the gates can differ even where the Icarus simulator and Verilator agree, and the verdict can change after an edit somewhere else; only a parameter that the simulator and Verilator work out differently is refused) and anything else that depends on the order a simulator runs events. Gate-level simulation (`run_tests` with `gl=true`) is the check against simulation behaviour, so run both.
- **Not equivalent**: a **counterexample**: the output bit, the clock cycle and the two values (RTL against gates). x means the value depends on how the flip-flop powered up. A difference between the RTL and the gates is only reported when it shows for a real power-up state of the gate flip-flops (0s and 1s), never only because an unknown spread through the gates. Fix the cause and run it again.
  If your RTL tests pass but the check says `not_equivalent`, the bug can be in the synthesis program rather than in your design. Run `run_tests` with `gl=true`: it runs your tests on the gates, and a failure there confirms that the gates really are wrong. One such bug is known: Yosys 0.62 (fixed in 0.68; LibreLane 3.0.14 and Tiny Tapeout use 0.62) builds wrong gates when a design holds an unsigned subtract wider than its two numbers (`a - b` into a wider result) and a signed compare of the same two numbers (`$signed(a) < $signed(b)`), whether they sit in one `case`, an `if` chain, a `?:` or separate assigns; Amaranth (`a.as_signed() < b.as_signed()`) and MyHDL (`a.signed() < b.signed()`) designs hit it too. The workaround is to write the signed compare from the sign bits: `lt = (a[3] != b[3]) ? a[3] : (a < b);` for 4-bit `a` and `b` (if the signs differ, the negative one is smaller; if they are the same, a plain compare gives the answer).
- **Not proven**: the proof could not be finished. Latches, a falling-edge or derived clock, tri-state outputs, flip-flops with both an asynchronous set and reset, RTL with constructs a simulator and the synthesis read differently (macros, also a back-tick or a double quote inside an escaped identifier such as ``\v`X``, `#` delays, `bit` or `int` (a loop index declared in the `for` header with no range, `for (int i = 0; ...)`, is fine), `unique`, `include`, a level list like `always @(a or b)`: write `always @(*)`; a combinational block that can read a signal before it writes it on some path through the block (`y = t; t = cnt;`, or a write in only one branch of an `if` before the read: write each signal at the top of the block, before any `if`, `case` or loop), a parameter whose value has an x or z bit (`1/0`, `5 % 0`, `0 ** -1`, a select outside the range such as `A[6]` of a 4-bit `A`, or a function that returns x), a parameter that the simulator and Verilator work out to different values in any instance, array element or loop pass, compared as whole numbers (a select outside the range inside a constant function, where an `if` or `!` turns the x into 0 or 1, or an untyped `localparam` that overflows, which the simulator makes wider) and any other constant that comes out x (`8'd5 / 8'd0`), a constant index outside the declared range (even on a signal such as `ui_in[9]`, or in a branch a constant never selects), a `/` or `%` by anything but a plain nonzero number (even a nonzero localparam, as in `W / B`), a `**` unless its base is a plain nonzero number or its exponent a plain number of 0 or more, and any call of your own function, in the condition of a generate `if`, `case` or `for` (work the value out in a `localparam` first), a call of your function with constant arguments anywhere but in the value of a parameter or `localparam` (not its range or type) when the function reads a vector at a computed index that the check cannot prove stays inside the declared range, or divides by a value it cannot prove is never 0, or when such a function gets an argument that can be x (an x or z bit, a divide, another call, any cast (also a size cast such as `3'(N)`: write `N[2:0]` instead), or a real: a literal such as `1.0e999`, a `real` or untyped parameter that holds one, `$sqrt(-1)`, because a simulator turns a real that is infinite or not a number into an all-x integer; a real or a hierarchical name such as `g.R` in an argument refuses ANY call of any of your functions, wherever it is: also in a `localparam` value or a parameter override (`localparam [7:0] K = g(R)`, where Verilator and a simulator can agree on a value that Yosys does not give), and also when the argument reads a signal or a loop variable too (`f(R + i)` in a loop, `f(R + a)` inside another function), because Yosys works such a call out itself and turns the real into a number without a warning (work an integer `localparam N = R;` out first and pass that; a `real` or untyped parameter whose value is not a plain number counts as a real, an untyped string and a `time` or typedef one too, while one declared `integer`, `logic` or `reg` or with a packed range such as `localparam [39:0] MSG = "HELLO"` holds bits and is fine; a hierarchical name such as `stage[i-1].q` of a generate loop refuses a call too: keep the stages in an array), and a real that is infinite or not a number is refused wherever it becomes an integer, in an `assign`, a function or a clocked block; only a parameter declared in code that no tool builds needs no value: a dead call with a live real or a real literal is still refused) (an index from a simple `for` loop over the vector's range, such as `for (i = 0; i < 8; i++)` on 8 bits, or an argument whose width fits, as a 2-bit index into 4 bits, is proven fine; a constant select of such an index, as `k[1:0]`, is fine too, with the range of its width only, not its loop's (the divisor `i[7:0] + 8'd1` of an 8-bit divide is not proven nonzero); a 3-bit index into 4 bits, a loop to `i <= 8` over 8 bits, `v[i - 1]` starting from `i = 0`, a loop step other than 1, a `while` loop (write it as a `for` loop), a `$bits` bound, any operator other than `+ - *` (a shift, `^`, `&`, `|`: write `v[7 - i]`, not `v[i ^ 7]`), a call as an argument, or another local or an array element as index, even through a select (`v[m[0]]`), is not: work such a call out in an integer `localparam` first; the same call in a `real` or string parameter is refused too), a select outside the range that Yosys finds once it puts a function in place or unrolls a loop, also one in an arm of `?:` that a constant condition never selects (guard it with an `if` instead), RTL that the Icarus simulator cannot compile or stops on with an internal error (a parameter of a typedef with two packed dimensions: use a plain vector), a number of 2**31 or more without a size (`2147483648`: write `32'd2147483648`), a function argument that is not an `input` (return the value instead), a function that reads anything other than its arguments, any task, a hierarchical function call (`g.f(a)`) or hierarchical name inside a function (`g.k`; a member of a struct argument such as `p.lo` is fine, unless `p` is also a label, module, instance or call name (a port name in a connection such as `.p(p)` does not count), or declared inside an `if`), an always block or function call that Yosys elaborates and Verilator does not (a generate branch or instance chosen by a constant that is x to Yosys and a simulator, or a call outside an always block that a constant makes dead, such as `EN ? f(a) : a`, `EN && f(a)` or `f(a) & {8{EN}}`: choose with a generate `if` instead); in MyHDL the core must be the plain single module MyHDL writes and a level-sensitive block must have the plain shape `@always_comb` writes and list every signal it reads, so use it), and proofs that run out of time all end here. A state machine that synthesis re-encodes can end here too (`unproven_points`): a limit of the proof, not a bug in your design. It is **not** a pass, and the agent must say so.

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
