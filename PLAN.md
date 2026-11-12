# Siliconsmith: plan for an open-source AI chip-design agent

**Status:** Planning. This public repo is a starter kit (plan, issues, and rules). No implementation code yet.
**Owner:** Adam Engelbrecht (GitHub `RFingAdam`).
**Repo:** [`RFingAdam/siliconsmith`](https://github.com/RFingAdam/siliconsmith)
**License:** Apache-2.0
**Plan date:** 2026-11-11 (CT). Tool licenses were checked against official sources on that date (see `THIRD_PARTY_LICENSES.md`). Anything marked *(unverified)* still needs a check.

---

## 1. Summary

Siliconsmith is an MCP server plus a skill pack and a pinned Docker toolchain. It lets an AI coding agent such as Claude Code, Cursor or Codex design small chips using only open-source tools.

One server, one toolchain image, and one agent loop serve **three tracks**:

```
Shared:  agent <-> MCP server (structured JSON) <-> pinned Docker toolchain
         write -> run tools -> parse errors / reports -> fix -> repeat
```

1. **Digital** (start here, milestone M0 first):

   ```
   idea -> RTL in Amaranth OR MyHDL (Python) -> Verilog
        -> cocotb tests (Icarus / Verilator)
        -> Yosys synthesis -> LibreLane / OpenROAD layout on sky130
        -> GDS that fits a Tiny Tapeout tile
        -> TT submission repo (GitHub Actions builds the real GDS)
   ```

2. **Analog/RF**, toward a Bluetooth-style chip that has a digital half and an RF/analog half:

   ```
   idea -> Xschem schematic -> ngspice sim
        -> Magic or KLayout layout -> netgen LVS / DRC
        -> openEMS for EM of passives
        -> TT analog-tile export (owner submits by hand)
   ```

   First design targets: an oscillator (ring or similar), a VCO, then an LNA. A working 2.4 GHz Bluetooth radio is **not** a goal. Section 8 states the limits honestly.

3. **SAW/BAW acoustic filters** (research / stretch):

   ```
   research note -> Mason / BVD equivalent-circuit models
                 -> Elmer FEM (piezo, if it checks out) and/or openEMS (EM only)
                 -> ladder-filter simulation
   ```

   Design and simulation only. There is no open fab path for these devices in this project.

Every tool returns compact, parsed JSON instead of raw logs, so the loop fits in a model's context window.

> Naming note: Efabless shut down in 2025. The `efabless/openlane2` repo now redirects to `chipfoundry/openlane2`, and the community continues it as **LibreLane** (`librelane/librelane`, Apache-2.0). Tiny Tapeout's GDS action installs LibreLane (`pip install librelane==3.0.x`; see [tt-gds-action](https://github.com/TinyTapeout/tt-gds-action/blob/main/action.yml)). **We target LibreLane.** In this plan, "OpenLane 2" means LibreLane.

## 2. Goals

1. **One MCP server** for all three tracks, with a small set of tools (section 5).
2. **A reproducible toolchain** in a pinned Docker image. Nothing gets installed on the host except Docker and Python.
3. **Agent-friendly feedback.** Errors, test failures, timing, area, utilization, DRC/LVS, and SPICE plot summaries come back as short structured results that point to files and line numbers where they exist.
4. **Skills (playbooks)** for the TT pin interface, coding rules, analog reality, and debug procedures.
5. **Beginner friendly.** The owner is new to digital HDL, so every example includes a plain-English README.
6. **Clean licensing.** The repo is Apache-2.0. GPL and LGPL tools are only called as external programs, never copied into the repo (`THIRD_PARTY_LICENSES.md` and section 10).
7. **Digital track:** two Python HDL front-ends (Amaranth or MyHDL) plus plain Verilog/SystemVerilog, a Tiny Tapeout-ready export, and the full sim → synth → layout loop.
8. **Analog/RF track:** schematic, SPICE, layout, LVS/DRC, a first oscillator/VCO and LNA, and a TT analog-tile export. Honest documentation of what will not fit on open PDKs or TT.
9. **Acoustic track:** open simulation of SAW/BAW-style resonators and filters, with no claim of fabrication.

## 3. Non-goals

- No commercial EDA (Cadence, Synopsys, Siemens, Keysight, COMSOL, etc.).
- No automatic purchase or submission to Tiny Tapeout or any foundry. Ordering and paying is always done by hand by the owner.
- No hosted service. Users run it locally or in their own CI.
- No new LLM training or fine-tuning.
- No large SoCs. The digital target is about 1 to 8 TT tiles, roughly 1,000 to 8,000 gates.
- No claim of a working 2.4 GHz Bluetooth radio, especially not on a Tiny Tapeout analog tile.
- No SAW/BAW, MEMS, or packaging fabrication. The acoustic track is simulation only.
- Do not copy code from unlicensed repositories (including [ssql2014/mcp4eda](https://github.com/ssql2014/mcp4eda)).

## 4. Tracks and build order

Implement **digital M0 first**. CI must be green before the next milestone on any track. After M0, prefer finishing digital M1–M3 (a real GDS and a TT-ready export) before deep analog or acoustic work, unless the owner says otherwise.

| Track | Label | Milestones | Starts after |
|---|---|---|---|
| Digital | `track:digital` | M0–M5 | Immediately (M0) |
| Analog/RF | `track:analog-rf` | A0–A4 | Digital M0 green |
| Acoustic (research/stretch) | `track:acoustic` | S0–S2 | Digital M0 green; not ahead of analog A0 |

## 5. Architecture

```
 ┌──────────────┐  MCP (stdio / streamable HTTP)  ┌────────────────────────────────────┐
 │ AI agent      │ ─────────────────────────────▶ │ siliconsmith MCP server (Python)    │
 │ Claude Code / │ ◀───────────────────────────── │  - project model (siliconsmith.toml)│
 │ Cursor/Codex  │   JSON results + file paths    │  - front-end plugins (digital)      │
 └──────────────┘                                 │  - analog / acoustic runners        │
        ▲  skills/ (playbooks, TT rules)          │  - runners (subprocess/docker)      │
        │                                          │  - report parsers -> JSON           │
                                                   └────────────────┬───────────────────┘
                                                                    │ docker run (pinned image)
                          ┌─────────────────────────────────────────┴──────────────────────────┐
                          │ siliconsmith-toolchain image                                        │
                          │ Digital: iverilog, verilator, yosys, cocotb, librelane              │
                          │          (+ openroad, magic, klayout, netgen via LibreLane/Nix)     │
                          │          sky130A via ciel                                            │
                          │ Analog:  xschem, ngspice, magic, klayout, netgen, openEMS           │
                          │ Acoustic: elmerfem, openEMS, ngspice (for BVD circuits)             │
                          └────────────────────────────────────────────────────────────────────┘
```

### 5.1 Common result shape and safety

Every tool returns `{ok, summary, details, artifacts[], next_hints[]}`. `summary` is 20 lines or fewer. `artifacts` are paths inside the project.

There is no generic shell tool. The server runs only fixed, allow-listed commands inside the container, and all paths are sandboxed to the project dir.

### 5.2 Digital front-end interface

```python
class Frontend(Protocol):
    name: str                      # "amaranth" | "myhdl" | "verilog"
    def check(self, project) -> Diagnostics            # import / elaborate, catch Python errors
    def emit_verilog(self, project) -> EmitResult      # -> build/rtl/<top>.v, top name, port list
    def native_sim(self, project, tb) -> SimResult     # optional: Amaranth sim / MyHDL sim
```

- **Amaranth:** `amaranth.back.verilog.convert(...)`. Amaranth's Verilog output uses Yosys internally *(confirm in M0 how the Amaranth version we pin calls Yosys: built-in amaranth-yosys wheel vs system binary)*.
- **MyHDL:** `toVerilog` / `block.convert(hdl='Verilog')`.
- **Verilog/SV:** the files pass straight through after a Verilator lint.
- Every digital front-end produces the same TT top-level wrapper, `tt_um_<name>`, using the fixed pin set (`ui_in[7:0]`, `uo_out[7:0]`, `uio_in/out/oe[7:0]`, `ena`, `clk`, `rst_n`). This was verified against [ttsky-verilog-template/src/project.v](https://github.com/TinyTapeout/ttsky-verilog-template/blob/main/src/project.v).

### 5.3 Digital MCP tools

| Tool | Inputs | Outputs |
|---|---|---|
| `toolchain_info` | none | Tool versions, PDK name and version, Docker image digest, health status |
| `create_project` | `name`, `frontend` (amaranth\|myhdl\|verilog), `pdk` (sky130A default; gf180mcuD, ihp-sg13g2 later), `tiles` (1x1…8x2), `clock_hz` | Project dir, `siliconsmith.toml`, wrapper stub, cocotb test stub, TT `info.yaml` skeleton |
| `list_files` / `read_file` | `path`, optional line range | File contents (limited to the project dir) |
| `write_rtl` | `path`, `content` or a unified `patch` | Write result plus an immediate `check` from the front-end (syntax/elaboration errors with line numbers) |
| `emit_verilog` | `project` | Path to the generated Verilog, top module, ports, lint warnings (Verilator `--lint-only`) |
| `write_test` | `path`, `content` | Saves a cocotb test (Python) |
| `run_tests` | `simulator` (icarus\|verilator), `test_filter`, `waves` (bool), `gl` (bool, gate-level) | Pass/fail per test, first failing assertion, signal values around the failure, VCD/FST path |
| `inspect_waves` | `vcd_path`, `signals[]`, `time_range` | Compact value table (avoids dumping whole waveforms) |
| `synthesize` | `target` (generic\|sky130), `flatten` | Cell count, area µm², latches found, multi-driven nets, combinational loops, utilization estimate vs the tile budget |
| `run_layout` | `stage` (full\|floorplan\|place\|cts\|route\|signoff), `config_overrides` | Run id, status, failing step, WNS/TNS per corner, DRC/LVS/antenna counts, utilization, GDS path |
| `read_report` | `run_id`, `kind` (timing\|drc\|lvs\|antenna\|area\|power\|log), `top_n` | Parsed worst items (e.g. 5 worst timing paths with start/end points) |
| `render_layout` | `run_id`, `format` (png\|svg) | KLayout-rendered image path (CLI only; KLayout is GPL) |
| `export_tt` | `repo_dir`, `author`, `description`, `pinout{}` | TT-format repo (`info.yaml`, `src/`, `test/`, `docs/info.md`, `.github/workflows`), precheck result |
| `tt_precheck` | `repo_dir` | Result of TT's local precheck *(confirm M3: run tt-support-tools precheck locally vs only in Actions)* |
| `explain` | `topic` (e.g. "setup violation", "latch inferred") | Short beginner explanation from the bundled skill docs |

### 5.4 Analog/RF MCP tools (added in A0–A3)

| Tool | Role |
|---|---|
| `create_analog_project` | TT analog-template-compatible skeleton (1x2 or 2x2, pinout including `ua[0..5]`) |
| `write_schematic` / `import_schematic` | Save or check an Xschem schematic (files only; we do not ship a GUI session) |
| `run_spice` | Run ngspice on a netlist; return structured DC/AC/tran summaries, not raw logs |
| `read_spice_plot` | Compact tables / PNG of named traces and a frequency or time range |
| `run_analog_layout` | Magic and/or KLayout CLI for DRC on a layout |
| `run_lvs` | netgen LVS, structured mismatches |
| `run_em` | openEMS job for a small passive; return S-params or field summary if the run succeeds |
| `export_tt_analog` | TT analog-template repo; no automatic order |

### 5.5 Acoustic MCP tools (added in S0–S2)

| Tool | Role |
|---|---|
| `bvd_model` / `mason_model` | Build our Apache-2.0 equivalent-circuit netlist from resonator parameters |
| `run_filter_sim` | Simulate a ladder or similar filter in ngspice; return S-params / insertion loss |
| `run_fem` | Elmer (or a documented fallback) for a simple resonator; structured summary |
| `run_em` | Shared with the analog track; do not claim openEMS solves piezoelectric acoustics |

### 5.6 Skills (playbooks shipped in `skills/`)

- `chip-design-loop.md`: the write → emit → lint → test → synth → layout → fix loop, with stop rules (max iterations, and stop and ask the user when a requirement is unclear).
- `tt-rules.md`: TT pin interface, `clock_hz`, tile sizes, "assign all outputs", 66 MHz IO limit ([TT clock spec](https://tinytapeout.com/specs/clock/)).
- `amaranth-style.md`, `myhdl-style.md`: beginner patterns (counter, FSM, shift register), common mistakes.
- `debug-timing.md`, `debug-drc.md`: how to read the parsed reports and the usual fixes (pipeline a stage, lower the clock, raise tiles, adjust `PL_TARGET_DENSITY`).
- `analog-reality.md`: section 8 of this plan, so agents give honest answers.
- `analog-flow.md`: Xschem → ngspice → Magic/KLayout → netgen, TT analog pins and templates.
- `acoustic-reality.md`: section 9; sim only, no fab, and what each solver can and cannot do.

## 6. Docker toolchain

- One image, `ghcr.io/rfingadam/siliconsmith-toolchain:<date-tag>`, built from `docker/Dockerfile`:
  - Base Ubuntu 24.04 + Python 3.12.
  - From distro packages or source (pinned versions): `iverilog`, `verilator`, `yosys`.
  - pip: `cocotb`, `amaranth`, `myhdl`, `librelane==<version TT pins>`.
  - LibreLane brings its own OpenROAD/Magic/KLayout/Netgen through its Nix or Docker setup. *(M2 decision: use LibreLane's official container directly as a second image instead of rebuilding it. This is probably simpler and closer to TT. Unverified until tried.)*
  - PDK: `ciel` (Apache-2.0, [fossi-foundation/ciel](https://github.com/fossi-foundation/ciel); the volare successor used by LibreLane 3) fetches `sky130A` at the exact version TT's action uses.
  - Analog/acoustic tools (Xschem, ngspice, Magic, netgen, openEMS, Elmer) are added to the image in A0 / S1, not in digital M0. M0 may ship a sim-only image.
- The MCP server itself is a small Python package (`pip install siliconsmith`) that calls `docker run` with the project mounted. A `--native` mode can use tools already on PATH.
- We don't modify any GPL/LGPL tool, and the repo contains no tool binaries or sources. Our image *distributes* those binaries, so publish the image with `THIRD_PARTY_LICENSES.md` and source links (GPL requires source availability for distributed binaries; using distro packages that ship source is the easiest route).
- Expected image size is several GB once the PDK is included *(unverified)*. Offer an option to mount the PDK from a cache volume.

## 7. Repo layout (target, once implementation starts)

```
siliconsmith/
├─ LICENSE                      # Apache-2.0
├─ THIRD_PARTY_LICENSES.md
├─ README.md
├─ CLAUDE.md
├─ PLAN.md
├─ ISSUES.md
├─ pyproject.toml               # package siliconsmith; extras: [amaranth], [myhdl]
├─ src/siliconsmith/
│  ├─ server.py                 # MCP server (official MCP Python SDK, MIT)
│  ├─ project.py                # siliconsmith.toml model
│  ├─ frontends/{base,amaranth_fe,myhdl_fe,verilog_fe}.py
│  ├─ runners/{docker,native}.py
│  ├─ tools/{rtl,sim,synth,layout,report,tt,analog,acoustic}.py
│  └─ parsers/…
├─ skills/
├─ docker/Dockerfile
├─ examples/
│  ├─ digital/   00_counter_amaranth/ … 05_dds_nco/
│  ├─ analog/    00_inverter/  01_ring_osc/  02_vco/  03_lna/
│  └─ acoustic/  00_bvd_resonator/  01_ladder_filter/
├─ tests/
├─ docs/
└─ .github/workflows/{ci.yml,nightly-layout.yml,image.yml}
```

Until M0 lands, this repo holds only the plan-kit files (README, LICENSE, PLAN, ISSUES, CLAUDE, THIRD_PARTY_LICENSES, CI placeholder, .gitignore).

## 8. Analog/RF track (toward a Bluetooth-style chip)

**How a Bluetooth chip splits up.**

- **Digital half:** a CPU core (often an Arm Cortex-M), memory, the Bluetooth link-layer/baseband logic, the modem's digital signal processing (GFSK modulation/demodulation, filters) and peripherals (UART, SPI). This half *is* the RTL → synthesis → layout flow the digital track automates. A realistic open-source slice here is an NCO/DDS, a GFSK modulator, or a small CPU example — not a full certified stack.
- **RF/analog half:** LNA, mixer, PLL/VCO (frequency synthesizer at 2.4 GHz), PA, ADC/DAC, plus the matching network, balun and antenna interface. This half is transistor-level analog: schematic, SPICE, hand-drawn or scripted layout, DRC/LVS, parasitics, and EM for passives.
- The two halves are joined on one die ("mixed-signal SoC") and verified together. That integration is an A4 stretch, not an early milestone.

**Open-source flow we will automate:** Xschem (schematic) → ngspice (simulation) → Magic or KLayout (layout, CLI) → netgen (LVS), using the sky130 / GF180 / IHP SG13G2 PDKs. openEMS for EM of a simple inductor, transmission line, or matching network.

**What's realistic on open PDKs / Tiny Tapeout.**

- Digital blocks (CPUs, UARTs, NCOs, digital filters) fit TT tiles. TT tiles hold about 1,000 gates each ([FAQ](https://www.tinytapeout.com/faq/)), with up to 8x2 tiles, and the IO tops out near 66 MHz ([clock spec](https://tinytapeout.com/specs/clock/)).
- Analog: TT offers analog slots (1x2 or 2x2 tiles, up to 6 analog pins `ua[0..5]`, 1.8 V or 3.3 V templates) on sky130 ([analog specs](https://tinytapeout.com/specs/analog/)), and it has analog templates for GF180 and IHP too.
- People do build op-amps, comparators, bandgaps, ring oscillators and simple LNAs/VCOs this way. A **working 2.4 GHz Bluetooth radio is not realistic** on a TT analog tile. Sky130 is a 130 nm process with limited RF transistor models, TT's pads and package aren't designed for GHz signals, and the space is tiny. *(This is a judgement from the specs, not something we have measured.)* IHP SG13G2 is a SiGe BiCMOS process aimed at RF, so it's the more promising open PDK for RF experiments *(unverified how well TT's IHP pads handle GHz)*.
- **First targets:** a ring oscillator (or similar oscillator) through layout; a VCO (circuit + sim, layout if it fits); an LNA (circuit + sim, with honest bandwidth and noise figures for the PDK). Then a TT analog-tile export of whichever block is cleanest. No automatic order.

## 9. Acoustic track (SAW/BAW): simulation only

SAW and BAW filters are piezoelectric devices: sound waves in special crystal or thin-film materials (lithium tantalate, aluminium nitride, and similar). They are built in dedicated MEMS/acoustic fabs, not on a normal CMOS Tiny Tapeout shuttle, and they are usually designed with acoustic and EM simulators (FEM, COM models, commercial tools such as COMSOL or vendor flows).

**This track does not fabricate anything.** There is no open SAW/BAW fab in the toolchain, and we will not pretend otherwise.

What we *can* try, in the open:

- **Mason and Butterworth-Van Dyke (BVD) models** implemented by us (Apache-2.0) and simulated in ngspice or Python. Good for ladder-filter circuit studies.
- **Elmer FEM** (GPL-2.0-or-later) for a piezoelectric resonator, *if* we can verify in S1 that the piezo solver and our meshing path actually run and produce a plausible response.
- **openEMS** (GPL-3.0) is an **electromagnetic** FDTD solver. It is the right tool for EM passives in the analog track. It is **not** a piezoelectric acoustic solver. Do not advertise it as a SAW/BAW device simulator. An optional S1 example may use it for a related EM structure, with that limit stated in the README.

Success for this track is a documented, reproducible simulation flow and honest skills — not a mask set.

## 10. Licensing

Repo license: **Apache-2.0**. It matches LibreLane, the PDKs and Tiny Tapeout templates. It includes a patent grant, which matters for hardware, and it is compatible with how we invoke GPL/LGPL tools (as separate processes).

The verified tool table lives in `THIRD_PARTY_LICENSES.md` (checked 2026-11-11 against official license files). Summary of the rules:

1. GPL tools (Icarus, KLayout, Xschem, netgen, openEMS, Elmer, and any other GPL tool we add) are only ever called as **separate processes** (subprocess/docker). Never import, link, or copy their source.
2. LGPL (MyHDL, Verilator) is fine as an unmodified dependency or external binary. Keep MyHDL an *optional extra* (`pip install siliconsmith[myhdl]`).
3. The repo contains no tool source or binaries. The Docker image is a separate distribution artifact with `THIRD_PARTY_LICENSES.md` plus source links.
4. Example designs and generated TT repos are Apache-2.0 with SPDX headers (`// SPDX-License-Identifier: Apache-2.0`), matching the TT template.
5. Add a DCO (`Signed-off-by`) for outside contributors. That's lighter than a CLA. Do not add bot credit or AI `Co-Authored-By` trailers to commits.
6. Do not copy code from unlicensed repos, including mcp4eda.

*Not legal advice.* This follows standard open-source practice, and it is fine for a hobby project.

## 11. Digital milestones and acceptance criteria

Full issue text is in `ISSUES.md`.

| M | Scope | Acceptance criteria |
|---|---|---|
| **M0** Hello-world sim | Repo skeleton, Docker image (sim tools only), `create_project`, `write_rtl`, `emit_verilog`, `write_test`, `run_tests`, Amaranth + MyHDL + Verilog front-ends | An agent in Claude Code or Cursor, starting from an empty folder and given only "make an 8-bit counter on uo_out, enable on ui_in[0]", produces RTL in **each** front-end that passes a cocotb test on Icarus **and** Verilator. Failing tests return the first failing assertion in ≤20 lines. CI green. |
| **M1** Synthesis | `synthesize`, sky130 liberty mapping, latch/loop/multi-driver detection, tile-budget estimate | The counter synthesizes to a sky130_fd_sc_hd netlist and reports cell count and area. A seeded latch bug is reported with its file and line. Gate-level sim (`run_tests gl=true`) passes. |
| **M2** Layout/GDS | `run_layout`, `read_report`, `render_layout` via LibreLane on sky130A, at TT 1x1 dimensions | The counter produces a GDS with 0 DRC, 0 LVS and 0 antenna errors (or antenna results reported clearly), and no setup or hold violations at 50 MHz. A seeded too-big design reports a utilization failure with a suggested fix. The PNG renders. Runtime on a normal laptop is recorded. |
| **M3** Tiny Tapeout ready | `export_tt`, `tt_precheck`, docs generation | The exported repo, pushed to a test GitHub repo **by the owner**, passes TT's `gds`, `precheck` and `gl_test` Actions (workflow verified in [ttsky-verilog-template](https://github.com/TinyTapeout/ttsky-verilog-template/blob/main/.github/workflows/gds.yaml)). `info.yaml` pinout is filled in, and `docs/info.md` explains how to test. No purchase is made automatically. |
| **M4** Stretch examples | UART TX/RX, SPI peripheral, PWM/servo, NCO/DDS (an RF-flavoured digital block), tiny RISC-V (RV32E subset, multi-tile) | Each example passes M0 through M3 checks in CI (layout nightly). The RISC-V example runs a small program in cocotb. A benchmark script reports the agent's success rate across about 10 prompts. |
| **M5** (optional) More PDKs | `pdk=gf180mcuD`, `pdk=ihp-sg13g2` (TT has `ttgf-*` and `ttihp-*` templates); docs that point at the analog/RF track | The counter hardens on at least one extra PDK. Digital skills point at the analog track instead of pretending analog is "docs only." |

### Digital example designs

| # | Design | Why it's here | Tiles (est., unverified) |
|---|---|---|---|
| 00 | 8-bit counter (×3 front-ends) | Hello world; proves the pipeline | 1x1 |
| 01 | PWM / LED dimmer | First "useful" output pin behavior | 1x1 |
| 02 | UART TX (+RX) | Talk to a PC through the TT demo board | 1x1 |
| 03 | SPI peripheral w/ registers | Typical chip-to-chip bus | 1x1–1x2 |
| 04 | Tiny RISC-V (RV32E subset) or 8-bit CPU | "A processor core" for the Bluetooth-SoC question | 2x2+ |
| 05 | NCO / DDS + CORDIC | RF-adjacent digital: generates a digital sine; ties to the owner's RF background | 1x2 |
| 06 | (later) FIR filter / digital down-converter | Digital half of a radio's signal chain | 2x2+ |

## 12. Analog/RF milestones

| A | Scope | Acceptance criteria |
|---|---|---|
| **A0** Analog hello-world sim | Xschem + ngspice in the image; `create_analog_project`, `run_spice`, `read_spice_plot` | An inverter or current mirror on sky130 (or the PDK we pin) has a schematic/netlist, a DC or transient sim that returns a structured summary, and a failing sim that names the error in ≤20 lines. All tools called as external processes. |
| **A1** Layout and LVS | Magic and/or KLayout CLI DRC; netgen LVS; optional extraction and post-layout spice | The A0 cell lays out at a TT-relevant size, DRC and LVS results are parsed, and a seeded LVS break is reported clearly. KLayout is CLI-only (GPL). |
| **A2** First targets: oscillator, VCO, LNA | Ring (or similar) oscillator through layout; VCO and LNA at least through schematic + spice | Oscillator: schematic, sim of period/frequency, layout + DRC/LVS. VCO: topology, tuning curve from spice, documented limits. LNA: gain/NF/match from spice (and PDK reality), documented limits. No 2.4 GHz radio claim. |
| **A3** EM + TT analog export | openEMS on a small passive; `export_tt_analog` | One inductor, T-line, or matching network has a documented openEMS run (or a written, evidence-based reason we cannot). A chosen analog block exports as a TT analog-template repo. The owner, not the agent, would submit. |
| **A4** Mixed-signal stretch | Architecture note + a small digital/analog co-sim hook | A short doc maps Bluetooth-style digital vs RF halves onto what we can actually build. Optional: one co-sim at a simple interface (e.g. NCO digital bits driving a simple analog block). Still no radio claim. |

## 13. Acoustic milestones (research / stretch)

| S | Scope | Acceptance criteria |
|---|---|---|
| **S0** Research and circuit models | Reality note; Mason/BVD models in our Apache-2.0 code | A short doc explains why there is no open fab, and what each solver can do. A BVD (and Mason, if we implement both) resonator simulates in ngspice or Python and returns impedance / S-params. |
| **S1** FEM / EM experiments | Elmer piezo resonator if it works; honest openEMS scope | Either a reproducible Elmer resonator example with a plausible response, or a documented failure with evidence. openEMS, if used, is labeled as EM-only. |
| **S2** Filter stretch | Ladder (or similar) filter from BVD resonators; optional FEM comparison | A multi-resonator filter sim meets a written spec (band, ripple, or rejection) or explains the gap. Optional: circuit model vs FEM for one resonator. Still sim only. |

## 14. CI

- `ci.yml` (every PR): after M0, ruff + mypy, unit tests (parsers against saved real logs as fixtures), M0/M1 integration tests in the toolchain image (counter in 3 front-ends, Icarus + Verilator, Yosys synth). Target under 10 minutes *(unverified)*. Until then, the placeholder lint job must stay green.
- `nightly-layout.yml`: LibreLane hardening of digital examples on sky130A at TT tile sizes, uploads GDS/PNG/metrics as artifacts, and fails if DRC/LVS/timing regress.
- `image.yml`: builds the toolchain image weekly and on Dockerfile changes, pins digests, and runs a smoke test.
- `tt-compat.yml` (M3): runs the exported counter repo through `TinyTapeout/tt-gds-action` (same action TT uses), pinned to the current shuttle tag (e.g. `@ttsky26d`).
- Agent benchmark (manual workflow): runs a fixed prompt set against a chosen model and records pass rate and iterations. It needs an API key secret and **only runs when triggered by hand** to control cost.

## 15. Prior art and how this differs

| Project | What it is | Difference from Siliconsmith |
|---|---|---|
| [Chip-Chat (NYU, arXiv 2305.13243)](https://arxiv.org/abs/2305.13243) | Conversational GPT-4 hardware design; a processor taped out via Tiny Tapeout/Efabless | Human-in-the-loop chats, not a reusable tool |
| [AutoChip](https://github.com/shailja-thakur/AutoChip) ([paper](https://arxiv.org/abs/2311.04887)) | LLM generates Verilog, compile/sim errors fed back automatically; tree search | Verilog-only and sim-only; no synthesis/layout/TT; research scripts; no license file on GitHub as of 2026-11-11 |
| [VerilogEval (NVlabs)](https://github.com/NVlabs/verilog-eval) (MIT) | Benchmark for LLM Verilog generation | A benchmark, not a tool. We can borrow prompts for our agent benchmark (MIT allows that). |
| [RTLLM](https://github.com/hkust-zhiyao/RTLLM) (MIT) | RTL-generation benchmark | Same as above |
| [mcp4eda](https://github.com/ssql2014/mcp4eda) | Collection of Node.js MCP servers (Yosys, Verilator, OpenLane, KLayout, …) | Closest prior art. Separate servers per tool, Verilog-only, no Python HDL front-ends, no TT export. **No license on GitHub** (checked 2026-11-11; last push Jul 2025), so we cannot reuse its code. Targets classic OpenLane, not LibreLane. |
| [mcp-yosys](https://github.com/zesun33/mcp-yosys) | Yosys MCP server (latch check, equivalence) | Synthesis only. Its latch/equivalence tool ideas are worth matching. |
| [eda-mcp](https://github.com/LittleBlackCQ/eda-mcp) | Docker MCP: Yosys, Icarus, Verilator lint, waveform queries | Sim/synth only, no layout/TT; nice waveform-query design to learn from |
| [Chip-Design-Agent MCP](https://github.com/BARATHM111/Chip-Design-Agent---MCP-EDA-Server-for-Archestra) | FastMCP + Yosys/OpenROAD/OpenLane + GDS preview | Similar shape, but tied to the Archestra platform, Verilog-only, small hobby repo, no TT export, no license on GitHub as of 2026-11-11 |
| Vendor AI: Synopsys.ai / DSO.ai, Cadence Cerebrus/JedAI; NVIDIA ChipNeMo | Closed, commercial | Need expensive licenses |

**Our angle:** (1) Three tracks on one MCP server and one pinned toolchain. (2) Python HDL front-ends with a choice of Amaranth or MyHDL (none of the digital-only projects above do this, as far as we found). (3) Tiny Tapeout as a finish line for digital and analog tiles, not just a GDS. (4) LibreLane (current) instead of classic OpenLane. (5) Compact structured reports built for agent loops. (6) Beginner docs plus an honest analog/RF and acoustic guide. (7) A clean Apache-2.0 license, and no copied unlicensed code.

## 16. Tiny Tapeout facts (checked 2026-11-11)

- **Shuttles open** on [tinytapeout.com](https://tinytapeout.com/) on 2026-11-11: SKY26d, SKY26e (sky130), and GF26c (GF180). Close dates are shown as a JavaScript countdown on that page; exact close timestamps are *(unverified)* here. Plan for a 2027 shuttle if a 2026 close is too soon for M3.
- **Cost (official calculator and analog specs, 2026-11-11):** the [Tiny Tapeout calculator](https://app.tinytapeout.com/calculator?pcbs=1&tiles=1) at default view showed **€70 per digital tile**, **€300 per devkit PCB**, and **€15** worldwide economy shipping, totalling **€385** for 1 tile + 1 devkit + shipping. Early-bird pricing is limited and only for individuals ([tinytapeout.com](https://tinytapeout.com/)); the calculator has shuttle / identity / design-type controls that can change the total, so re-check it before any order.
- **Analog (sky130), from the official [analog specs](https://tinytapeout.com/specs/analog/):** projects with analog pins must be two tiles high, so the minimum area cost is €140 (2 × €70). Analog pins cost **€40 per pin for the first 2 pins** on a shuttle (per customer) and **€100 per pin** after that. Example on that page: 1x2 + 2 pins = €220 before ASIC, PCB, or shipping. Analog slots are limited per shuttle.
- **Size:** about 160×100 µm per digital tile, roughly 1,000 gates ([FAQ](https://www.tinytapeout.com/faq/)). Valid digital sizes include 1x1, 1x2, 2x2, 3x2, 4x2, 6x2, 8x2 (template `info.yaml`). Analog templates are 1x2 or 2x2 (about 160×225 µm and 334×225 µm on sky130A).
- **Languages:** TT's `info.yaml` lists Amaranth as an accepted `language` value. VHDL templates exist too.
- You get **one chip** per devkit. More chips require buying more devkits.
- Never auto-order. Prices and slots change; always re-check the calculator.

## 17. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Toolchain churn (Efabless shutdown → LibreLane; TT bumps LibreLane version per shuttle) | Flows break | Pin to the LibreLane version TT's action uses, follow TT shuttle tags, nightly CI |
| MyHDL less active than Amaranth; Verilog output quirks | Second front-end lags | The front-end interface is isolated; MyHDL is marked "beta" if needed |
| LLMs write subtly wrong RTL that still passes weak tests | Bad silicon | Skill requires test-first; gate-level sim; optional formal checks via SymbiYosys (later) |
| LibreLane runtime/RAM on laptops | Slow loop | Run layout only after sim and synth pass; cache PDK; small designs take minutes per TT FAQ |
| Large Docker image | Setup friction | PDK volume cache; separate sim-only image for M0/M1; add analog/FEM tools later |
| License mistakes (vendoring GPL code, or copying unlicensed code) | Repo relicensing pain | CI check: `reuse lint` / SPDX headers; rules in CLAUDE.md; never copy mcp4eda |
| Scope creep (full Bluetooth radio, SAW fab) | Never ships | Digital M0 first; radio and fab are explicit non-goals; skills state the limits |
| Elmer piezo or openEMS-for-acoustics may not do what we want | Acoustic track stalls | S1 accepts a documented failure; BVD models still land in S0 |
| Agent API cost in CI | Money | Agent benchmark only runs on manual trigger |
| Owner is new to HDL | Can't judge outputs | Beginner docs, explained examples, `explain` tool |
| Tapeout deadlines/prices change | Plan out of date | Always re-check the TT calculator before ordering; never auto-order |

## 18. Effort estimate (rough, unverified)

Digital: M0: 1–2 agent-days · M1: 1 day · M2: 2–4 days (LibreLane container integration is the hard part) · M3: 1–2 days · M4: about 1 day per example, RISC-V longer.

Analog: A0: 1–2 days · A1: 2–3 days · A2: several days per target · A3–A4: stretch.

Acoustic: S0: 1 day · S1–S2: research, open-ended.

## 19. Next steps

1. This repo is created. The owner clones it and points Claude Code at it in plan mode, digital M0 first (`CLAUDE.md`).
2. Implement M0 against the GitHub issues, keep CI green, then continue milestone by milestone.
