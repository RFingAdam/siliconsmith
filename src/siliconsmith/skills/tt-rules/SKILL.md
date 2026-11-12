---
name: tt-rules
description: Use when you need the Tiny Tapeout pin interface, the core and wrapper split, size and clock limits, or honest limits on what fits on a tile.
---

# Tiny Tapeout rules

Tiny Tapeout (TT) puts many small student and hobby designs on one shared chip. Every design sees the same fixed set of pins, so your design must fit that interface.

## Pins

Your design sees these signals (all single bits except the buses):

| Pin | Direction | Meaning |
|---|---|---|
| `clk` | in | The clock. |
| `rst_n` | in | Reset, active low: 0 means reset, 1 means run. |
| `ena` | in | Always 1 while your design is powered; you can ignore it. |
| `ui_in[7:0]` | in | 8 dedicated inputs. |
| `uo_out[7:0]` | out | 8 dedicated outputs. |
| `uio_in[7:0]` | in | 8 bidirectional pins, value coming in. |
| `uio_out[7:0]` | out | The same 8 pins, value you drive. |
| `uio_oe[7:0]` | out | Output enable per pin: 1 means that pin is an output, 0 means an input. |

Assign every output your core declares. Leave out the pins you do not use: the wrapper ties unused outputs (for example `uio_out` and `uio_oe`) to 0.

## Core and wrapper

You write the core: a module named after the project, with a subset of the pins above using exactly these names. The tool generates the `tt_um_<name>` wrapper in `src/project.v` and connects the core by name. Never edit the wrapper; `emit_verilog` overwrites it. Outputs you leave out are tied to 0.

## Clock and speed

Put the clock in `spec.yaml` as `clock_hz`; it is the only place the clock is written. The Tiny Tapeout IO limit is about 66 MHz (see https://tinytapeout.com/specs/clock/). A design clocked above that cannot be driven through the pins, even if the logic could run faster. Simulation does not check timing; timing checks come in a later milestone.

## Size

A 1x1 tile is 161.00 x 111.52 um, roughly a thousand gates of logic in the standard cell library. Small counters, state machines and shift registers fit easily. Sizes above 1x1 cost more. This milestone does not synthesize yet, so it cannot tell you the area.

## Style rules that keep the design buildable

- Synchronous logic only: registers change on the clock edge, reset is applied on the clock edge.
- No latches: in a combinational block, give every signal a value on every path.
- Each signal is driven from one place only.
- Do not make your own clocks by dividing a clock into a register output; use an enable instead.

## Honest limits

- A working 2.4 GHz radio (for example Bluetooth) is not realistic on any Tiny Tapeout tile, digital or analog. The sky130 RF models are limited, the Tiny Tapeout pads and package are not made for GHz signals, and the area is tiny. Digital tiles hold digital logic only. Small analog blocks such as an oscillator, a VCO or a low-noise amplifier are the realistic targets of the later analog track.
- SAW and BAW filters have no open fabrication path in this project.
- Passing simulation is not working silicon.
- Submitting to a shuttle, and paying for it, is always done by the owner by hand. Never try to order or submit anything.
