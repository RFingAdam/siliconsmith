# Siliconsmith: GitHub-ready milestone issues

Copy is already filed on the GitHub tracker with the labels shown. Do **not** add `agent-approved`.
Start with digital **M0** only. See `PLAN.md` for architecture, MCP tools, and licensing rules. See `CLAUDE.md` for implementation rules.

---

# Track: digital (`track:digital`)

## [M0-1] Repo skeleton, Apache-2.0 licensing, CI baseline
**Labels:** `track:digital` `milestone:M0` `infra`
**Goal:** Empty, buildable Python package with licensing done right from day one.
**Tasks**
- `pyproject.toml` (package `siliconsmith`, Python ≥3.11, extras `[amaranth]`, `[myhdl]`).
- Keep `LICENSE` (Apache-2.0) and `THIRD_PARTY_LICENSES.md`. Add `NOTICE` if needed and `CONTRIBUTING.md` with the GPL/LGPL rules (call GPL tools only as external processes; no vendoring) and DCO sign-off.
- SPDX headers on all source files; `reuse lint` in CI.
- Replace the CI placeholder: ruff, mypy, pytest.
**Acceptance criteria**
- [ ] `pip install -e .[amaranth,myhdl]` works on a clean Ubuntu 24.04 runner.
- [ ] CI is green, and `reuse lint` passes.
- [ ] The repo contains no third-party tool source or binaries.

## [M0-2] Toolchain Docker image (simulation stage)
**Labels:** `track:digital` `milestone:M0` `docker`
**Tasks**
- `docker/Dockerfile`: Ubuntu 24.04, Python 3.12, iverilog, verilator, yosys, cocotb, amaranth, myhdl (versions pinned).
- `image.yml` workflow publishes to `ghcr.io/rfingadam/siliconsmith-toolchain` with a date tag and a digest.
- `toolchain_info` MCP tool reports the versions.
**Acceptance criteria**
- [ ] `docker run <image> iverilog -V && verilator --version && yosys -V` all succeed in CI.
- [ ] `toolchain_info` returns JSON with every tool's version and the image digest.
- [ ] Image size is recorded in the README.

## [M0-3] MCP server core + project model
**Labels:** `track:digital` `milestone:M0` `mcp`
**Tasks**
- MCP server on the official MCP Python SDK (stdio, plus streamable HTTP optional).
- `create_project`, `list_files`, `read_file`, `write_rtl`, `write_test` with path sandboxing (no access outside the project dir, no generic shell tool).
- `siliconsmith.toml` schema: name, frontend, top, pdk, tiles, clock_hz.
- Standard result shape `{ok, summary, details, artifacts, next_hints}`.
**Acceptance criteria**
- [ ] `create_project(frontend=amaranth|myhdl|verilog)` creates a project with a TT-compatible `tt_um_<name>` wrapper (ports: `ui_in[7:0]`, `uo_out[7:0]`, `uio_in/uio_out/uio_oe[7:0]`, `ena`, `clk`, `rst_n`) and a cocotb test stub.
- [ ] Path traversal (`../`) attempts are rejected (unit test).
- [ ] README has working config snippets for Claude Code and Cursor.

## [M0-4] Front-end interface: Amaranth, MyHDL, Verilog passthrough
**Labels:** `track:digital` `milestone:M0` `frontend`
**Tasks**
- `Frontend` protocol (`check`, `emit_verilog`, optional `native_sim`).
- Amaranth: `amaranth.back.verilog.convert`. MyHDL: `convert(hdl='Verilog')`. Verilog: passthrough + `verilator --lint-only`.
- `emit_verilog` MCP tool.
**Acceptance criteria**
- [ ] The same 8-bit counter spec emits valid Verilog from all three front-ends with an identical top-level port list.
- [ ] A Python syntax error in Amaranth/MyHDL source comes back with file and line in ≤20 lines.
- [ ] A Verilator lint warning (e.g. width mismatch) is returned in a structured form.

## [M0-5] `run_tests` with cocotb on Icarus and Verilator
**Labels:** `track:digital` `milestone:M0` `sim`
**Tasks**
- `run_tests(simulator, test_filter, waves)`: runs cocotb via its runner, then parses `results.xml`.
- `inspect_waves(vcd_path, signals, time_range)` returns a compact value table.
**Acceptance criteria**
- [ ] Counter tests pass on **both** Icarus and Verilator for **all three** front-ends (CI matrix).
- [ ] A seeded bug (off-by-one wrap) produces a failure summary naming the test, the assertion, and the expected vs actual value.
- [ ] Waveform output stays under 50 rows unless more are requested.

## [M0-6] Skill pack v1 + end-to-end agent demo
**Labels:** `track:digital` `milestone:M0` `skills` `docs`
**Tasks**
- `skills/chip-design-loop.md`, `skills/tt-rules.md`, `skills/amaranth-style.md`, `skills/myhdl-style.md`.
- `docs/beginner-guide.md` + glossary (RTL, testbench, synthesis, netlist, GDS, DRC, LVS, slack).
**Acceptance criteria**
- [ ] A recorded session (transcript in `docs/demos/`): starting from an empty dir and the prompt "8-bit counter on uo_out, count enable on ui_in[0]", the agent reaches passing tests in each front-end without any human code edits.
- [ ] Number of loop iterations recorded per front-end.

---

## [M1-1] `synthesize` tool on sky130
**Labels:** `track:digital` `milestone:M1` `synth`
**Tasks**
- Yosys synthesis to generic gates and sky130_fd_sc_hd (liberty from PDK via ciel).
- Parse `stat`: cell count, area µm², flops vs combinational, estimated tile utilization (≈1,000 gates/tile guide from TT FAQ).
- Detect latches, combinational loops, multi-driven nets, and undriven outputs with source locations.
**Acceptance criteria**
- [ ] Counter synthesis reports cell count and area.
- [ ] Seeded latch (incomplete `if` in combinational logic) is reported with file and line.
- [ ] Estimate warns when the design likely exceeds the chosen `tiles`.

## [M1-2] Gate-level simulation
**Labels:** `track:digital` `milestone:M1` `sim`
**Acceptance criteria**
- [ ] `run_tests(gl=true)` reruns the cocotb tests against the synthesized sky130 netlist with cell models, and they pass for the counter.
- [ ] Mismatch between RTL and GL results is reported clearly.

---

## [M2-1] LibreLane integration (OpenLane 2 successor)
**Labels:** `track:digital` `milestone:M2` `layout`
**Tasks**
- Decide: reuse LibreLane's official container vs adding it to our image (document why).
- Pin the LibreLane and sky130A versions to the ones used by `TinyTapeout/tt-gds-action` for the current shuttle.
- `run_layout(stage=full|floorplan|place|cts|route|signoff, config_overrides)` with a TT 1x1 die-area config.
**Acceptance criteria**
- [ ] Counter → GDS on sky130A at 1x1 tile size, 0 DRC (Magic + KLayout), LVS clean, no setup/hold violations at 50 MHz.
- [ ] Runtime and peak RAM recorded for a typical laptop/CI runner.
- [ ] Failing step is named in `summary` when the flow fails.

## [M2-2] `read_report` parsers (timing, DRC, LVS, antenna, area, power)
**Labels:** `track:digital` `milestone:M2` `parsers`
**Acceptance criteria**
- [ ] Parsers are unit-tested against real saved LibreLane run fixtures.
- [ ] `read_report(kind=timing, top_n=5)` returns the worst paths with startpoint, endpoint, slack and corner.
- [ ] Seeded oversize design (e.g. 64-bit multiplier in 1x1) returns a utilization/placement failure plus `next_hints` (raise tiles, reduce logic).

## [M2-3] `render_layout`
**Labels:** `track:digital` `milestone:M2`
**Acceptance criteria**
- [ ] PNG of the final GDS is produced via the KLayout CLI (external process; GPL tool not imported).
- [ ] Image path is returned and linked from the run summary.

---

## [M3-1] `export_tt`: Tiny Tapeout submission repo
**Labels:** `track:digital` `milestone:M3` `tinytapeout`
**Tasks**
- Generate repo in the current `ttsky-verilog-template` format: `info.yaml` (title, author, description, `language` = Amaranth/MyHDL/Verilog, `clock_hz`, `tiles`, `top_module`, `source_files`, full pinout), `src/`, `test/` (cocotb), `docs/info.md`, `.github/workflows/gds.yaml` pinned to the current shuttle tag.
- For Amaranth/MyHDL: commit the generated Verilog plus the Python source (document which is authoritative).
**Acceptance criteria**
- [ ] Exported counter repo, pushed **by the owner** to a test repo, passes TT's `gds`, `precheck`, and `gl_test` Actions.
- [ ] `docs/info.md` explains how to test on the TT demo board in plain English.
- [ ] Nothing is ordered or submitted automatically.

## [M3-2] `tt_precheck` locally
**Labels:** `track:digital` `milestone:M3` `tinytapeout`
**Acceptance criteria**
- [ ] Runs TT's precheck (from `tt-support-tools` / `tt-gds-action`) locally or documents why only CI can run it.
- [ ] Failures are mapped to structured hints (e.g. missing pinout entry, wrong top module name).

## [M3-3] Docs: "Your first chip" walkthrough + cost note
**Labels:** `track:digital` `milestone:M3` `docs`
**Acceptance criteria**
- [ ] Step-by-step guide from idea to submission-ready repo.
- [ ] Cost section links the official [TT calculator](https://app.tinytapeout.com/calculator) and says prices change. As of 2026-11-11 the calculator default view showed €70/tile, €300/devkit, €15 shipping (1 tile + devkit + shipping = €385). Re-check before any order.

---

## [M4-1] Example: UART TX/RX
**Labels:** `track:digital` `milestone:M4` `example`
**Acceptance criteria**
- [ ] cocotb test sends/receives bytes at 115200 baud from a 10–50 MHz clock.
- [ ] Hardens at TT size in nightly CI; TT export passes precheck.

## [M4-2] Example: SPI peripheral with register file
**Labels:** `track:digital` `milestone:M4` `example`
**Acceptance criteria**
- [ ] cocotb SPI master test reads/writes 4+ registers (modes 0 and 3).
- [ ] Hardens and exports as above.

## [M4-3] Example: PWM and NCO/DDS (RF-flavoured digital)
**Labels:** `track:digital` `milestone:M4` `example`
**Acceptance criteria**
- [ ] NCO outputs a digital sine (LUT or CORDIC) on `uo_out`; cocotb checks frequency word → output period and spectral purity against a Python reference (numpy FFT).
- [ ] README explains in RF terms (phase accumulator, spurs, how a DAC would follow).

## [M4-4] Example: tiny CPU (RV32E subset or 8-bit)
**Labels:** `track:digital` `milestone:M4` `example` `stretch`
**Acceptance criteria**
- [ ] Runs a small test program (e.g. Fibonacci) in cocotb, with results on `uo_out`.
- [ ] Fits in ≤8x2 tiles (record actual size), hardens clean in nightly CI.

## [M4-5] Agent benchmark
**Labels:** `track:digital` `milestone:M4` `eval`
**Acceptance criteria**
- [ ] About 10 prompts (some adapted from VerilogEval/RTLLM, both MIT, with attribution) run through the MCP loop for a chosen model.
- [ ] Reports pass rate, iterations and token/cost per design. **Manual trigger only.**

---

## [M5-1] (optional) Extra PDKs: GF180MCU and IHP SG13G2
**Labels:** `track:digital` `milestone:M5` `pdk`
**Acceptance criteria**
- [ ] Counter hardens on at least one of `gf180mcuD` / `ihp-sg13g2` using TT's `ttgf-*` / `ttihp-*` template conventions.
- [ ] Confirm how current GF180 builds are fetched (Google's `gf180mcu-pdk` repo is archived; open_pdks/ciel is the expected path).

## [M5-2] Digital skills point at the analog/RF track
**Labels:** `track:digital` `milestone:M5` `docs` `skills`
**Acceptance criteria**
- [ ] `skills/analog-reality.md` (and the beginner docs) state the digital/analog split, point at the analog/RF issues, and do not treat analog as "docs only."
- [ ] The skill stays honest: no 2.4 GHz Bluetooth radio claim, no SAW/BAW fab claim.

---

# Track: analog/RF (`track:analog-rf`)

Work starts after digital M0 is green. First design targets: oscillator, VCO, LNA. All EDA tools are external processes. No automatic Tiny Tapeout order.

## [A0-1] Analog tools in the toolchain image
**Labels:** `track:analog-rf` `milestone:A0` `docker`
**Tasks**
- Add Xschem, ngspice, Magic, and netgen (KLayout may already be present from the digital image) as pinned packages or official binaries. Record versions in `toolchain_info`.
- Do not vendor their source. Update `THIRD_PARTY_LICENSES.md` if versions or source links change.
**Acceptance criteria**
- [ ] `docker run <image>` can invoke `xschem --version` or an equivalent non-GUI check, `ngspice -v`, `magic --version` (or `-dnull` help), and `netgen -noconsole` / version, all in CI.
- [ ] Licenses remain as verified in `THIRD_PARTY_LICENSES.md` (Xschem GPL-2.0-or-later, ngspice mixed BSD, Magic UC Berkeley-style, netgen GPL any version, KLayout GPL-3.0-or-later).

## [A0-2] Analog MCP project + SPICE tools
**Labels:** `track:analog-rf` `milestone:A0` `mcp`
**Tasks**
- `create_analog_project` produces a TT-analog-compatible skeleton (1x2 default, `tt_um_<name>`, `ua[0..5]` documented, `siliconsmith.toml` with `track=analog`).
- `run_spice` and `read_spice_plot` call ngspice as a subprocess and return `{ok, summary, details, artifacts, next_hints}`.
**Acceptance criteria**
- [ ] A project dir is created with a netlist stub and a test bench stub, paths sandboxed.
- [ ] A passing DC or transient run returns a compact summary (e.g. Vout, current, time points) in ≤20 lines.
- [ ] A seeded netlist error returns the ngspice error text, trimmed, with a file/line when present.

## [A0-3] Hello-world inverter or current mirror
**Labels:** `track:analog-rf` `milestone:A0` `example`
**Acceptance criteria**
- [ ] Schematic or explicit netlist of a sky130 (or the pinned PDK) inverter **or** current mirror, simulated with ngspice.
- [ ] README explains the circuit in plain English, including expected DC transfer or bias current, and how to re-run the sim via the MCP tools.
- [ ] Example is Apache-2.0 with SPDX headers; no vendor PDK files are committed (fetch via ciel at run time).

---

## [A1-1] Analog layout and DRC
**Labels:** `track:analog-rf` `milestone:A1` `layout`
**Acceptance criteria**
- [ ] The A0 cell is laid out with Magic and/or KLayout at a TT-relevant size (document the flow).
- [ ] DRC is run via CLI (KLayout must not be imported as a Python module). Results are parsed into a count + first N violations with locations.
- [ ] A seeded DRC error is reported clearly.

## [A1-2] netgen LVS, extraction, post-layout SPICE
**Labels:** `track:analog-rf` `milestone:A1` `layout`
**Acceptance criteria**
- [ ] `run_lvs` calls netgen as a subprocess and reports matching/mismatching nets and devices in a structured summary.
- [ ] A seeded LVS break (renamed or missing device) is reported with a hint.
- [ ] Optional but preferred: extracted parasitics re-simulated with ngspice, with a short before/after comparison.

---

## [A2-1] Ring oscillator (or similar oscillator) through layout
**Labels:** `track:analog-rf` `milestone:A2` `example`
**Acceptance criteria**
- [ ] Schematic + ngspice transient sim reports period and frequency (and how they were measured).
- [ ] Layout + DRC + LVS for the oscillator, sized with TT analog tiles in mind (1x2 or 2x2).
- [ ] README states expected frequency range on the chosen PDK and what would change on silicon.

## [A2-2] VCO: design and simulation
**Labels:** `track:analog-rf` `milestone:A2` `example`
**Acceptance criteria**
- [ ] A documented VCO topology (ring, relaxation, or LC if the PDK supports a credible tank) with a tuning curve from ngspice (frequency vs control).
- [ ] Limits are written down (tuning range, expected phase-noise quality is optional and marked unverified if not simulated).
- [ ] Layout is in scope if it fits a TT analog tile; otherwise the README says why it was left at schematic/sim.

## [A2-3] LNA: design and simulation
**Labels:** `track:analog-rf` `milestone:A2` `example`
**Acceptance criteria**
- [ ] A documented LNA topology on sky130 and/or IHP SG13G2, with ngspice results for gain and, if models allow, noise figure and input match.
- [ ] The README is honest about PDK and TT-package limits (including that a 2.4 GHz Bluetooth-quality LNA on a TT analog tile is not the bar).
- [ ] Layout optional; same rule as A2-2.

---

## [A3-1] openEMS for a simple passive
**Labels:** `track:analog-rf` `milestone:A3` `em`
**Acceptance criteria**
- [ ] One small EM example (inductor, transmission line, or matching network) runs openEMS as a subprocess, with a documented mesh/setup.
- [ ] Returns compact S-parameter or impedance results, or a written, evidence-based note if the run cannot be automated in CI (tool size, runtime, or license/source-distribution issues).
- [ ] README states openEMS is an electromagnetic FDTD solver.

## [A3-2] `export_tt_analog`
**Labels:** `track:analog-rf` `milestone:A3` `tinytapeout`
**Acceptance criteria**
- [ ] Exports a chosen analog block (prefer the oscillator if it is clean) in the current `ttsky-analog-template` format: `info.yaml` (tiles 1x2 or 2x2, `analog_pins`, pinout, `uses_3v3` if needed), GDS/LEF paths, `src/project.v` stub, `docs/info.md`.
- [ ] Nothing is ordered or submitted automatically. The owner would push and submit by hand.
- [ ] Docs link the official TT analog specs and calculator, and repeat that prices change.

---

## [A4-1] Bluetooth-style mixed-signal architecture note
**Labels:** `track:analog-rf` `milestone:A4` `docs` `stretch`
**Acceptance criteria**
- [ ] A short doc maps a Bluetooth-style SoC (digital baseband / modem / CPU vs LNA, mixer, VCO/PLL, PA) onto what this repo can actually build on open PDKs and TT.
- [ ] Explicit non-goal: a working 2.4 GHz Bluetooth radio, especially on a TT analog tile.
- [ ] Cross-links the digital NCO/DDS example and the analog oscillator/VCO/LNA issues.

## [A4-2] Optional digital + analog co-sim hook
**Labels:** `track:analog-rf` `milestone:A4` `stretch`
**Acceptance criteria**
- [ ] One documented co-sim at a simple interface (for example digital bits from the NCO example driving a simple analog block, or a Verilog/AMS-free mixed sim with a clear handshake) **or** a written explanation of why a clean co-sim is deferred.
- [ ] Still no radio claim, and no commercial simulator.

---

# Track: acoustic (`track:acoustic`)

Research and stretch. Design and simulation only. There is no open SAW/BAW fab in this project. Do not start serious work until digital M0 is green, and do not let this track outrun analog A0.

## [S0-1] Acoustic reality note
**Labels:** `track:acoustic` `milestone:S0` `docs` `research`
**Acceptance criteria**
- [ ] `docs/` + `skills/acoustic-reality.md` explain what SAW/BAW devices are, why they are not CMOS Tiny Tapeout parts, and that this track will not fabricate them.
- [ ] Each solver is described accurately: Mason/BVD are equivalent-circuit models we implement; Elmer may do piezoelectric FEM *(to be verified in S1)*; openEMS is EM FDTD, not a piezo acoustic solver.
- [ ] Licenses point at `THIRD_PARTY_LICENSES.md`.

## [S0-2] Mason / BVD models (our Apache-2.0 code)
**Labels:** `track:acoustic` `milestone:S0`
**Acceptance criteria**
- [ ] A BVD resonator model (and Mason, if both are implemented) is written in this repo under Apache-2.0, as Python and/or an ngspice subcircuit, with SPDX headers.
- [ ] A sim returns input impedance and/or S-parameters over a stated frequency range for a documented parameter set (fs, fp or C0, C1, L1, R1).
- [ ] Unit tests check the equivalent-circuit math against a closed-form point or two. No third-party unlicensed code.

---

## [S1-1] Elmer FEM piezoelectric resonator experiment
**Labels:** `track:acoustic` `milestone:S1` `fem` `research`
**Acceptance criteria**
- [ ] Elmer is invoked as an external process (GPL; never vendored) from the toolchain image or a documented install path.
- [ ] Either a reproducible simple resonator (geometry, mesh, piezo material assumptions, and a plotted response) **or** a documented failure with the commands and logs that show why (missing piezo support, mesh, or material model).
- [ ] If it works, results are compared qualitatively to the S0 BVD model, with gaps called out.

## [S1-2] openEMS scope (EM only) for the acoustic track
**Labels:** `track:acoustic` `milestone:S1` `em` `research`
**Acceptance criteria**
- [ ] The skill and example README state clearly that openEMS does not simulate piezoelectric SAW/BAW devices.
- [ ] Optional: one small related EM example (a transducer electrode pattern, a package cavity, or a feed) if it is useful and CI-tolerable; otherwise a short written decision not to go further.

---

## [S2-1] Ladder (or similar) filter from BVD resonators
**Labels:** `track:acoustic` `milestone:S2` `stretch`
**Acceptance criteria**
- [ ] A multi-resonator filter (e.g. ladder) is assembled from the S0 models and simulated.
- [ ] A written spec (center, bandwidth, ripple, or rejection) is either met or missed with the gap explained.
- [ ] Still simulation only; no fab or foundry language in the README.

## [S2-2] Optional circuit-model vs FEM comparison
**Labels:** `track:acoustic` `milestone:S2` `stretch`
**Acceptance criteria**
- [ ] If S1-1 produced a working Elmer resonator, compare its response to the BVD/Mason model for the same (or fitted) parameters and document the error.
- [ ] If S1-1 did not work, this issue is closed as deferred with a pointer to the S1-1 write-up.
