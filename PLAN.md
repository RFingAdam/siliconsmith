# Siliconsmith: plan for an open-source AI chip-design agent

**Status:** Implementation of digital milestone M0 is in progress. The tools described in this plan are not usable yet; `ISSUES.md` and the GitHub issues track what has landed.
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

Every tool returns compact, parsed JSON instead of raw logs, so the loop fits in a model's context window. A **validation sequence** (section 5.7) records which checks have passed for the current design files, so the owner can see what has been proven and what has not.

> Naming note: Efabless shut down in 2025. The `efabless/openlane2` repo now redirects to `chipfoundry/openlane2`, and the community continues it as **LibreLane** (`librelane/librelane`, Apache-2.0). Tiny Tapeout's GDS action installs `librelane==3.0.14` at the `ttsky26d` and `ttsky26e` shuttle tags (see [tt-gds-action](https://github.com/TinyTapeout/tt-gds-action/blob/ttsky26e/action.yml)). The latest LibreLane release is 3.0.17 (2026-10-26), but we pin the version TT uses. **We target LibreLane.** In this plan, "OpenLane 2" means LibreLane.

## 2. Goals

1. **One MCP server** for all three tracks, with a small set of tools (section 5).
2. **A reproducible toolchain** in a pinned Docker image. Nothing gets installed on the host except Docker and Python.
3. **Agent-friendly feedback.** Errors, test failures, timing, area, utilization, DRC/LVS, and SPICE plot summaries come back as short structured results that point to files and line numbers where they exist.
4. **Sandboxed execution.** Code the agent writes (HDL elaboration, tests) runs only inside a locked-down container, never in the server process (section 5.1).
5. **A validation sequence.** A written spec, tests tagged with the requirements they cover, and a ledger of results show which checks pass for the current files (section 5.7).
6. **Skills (playbooks)** for the TT pin interface, coding rules, analog reality, and debug procedures.
7. **Beginner friendly.** The owner is new to digital HDL, so every example includes a plain-English README.
8. **Clean licensing.** The repo is Apache-2.0. GPL and LGPL tools are only called as external programs, never copied into the repo (`THIRD_PARTY_LICENSES.md` and section 10).
9. **Digital track:** two Python HDL front-ends (Amaranth or MyHDL) plus plain Verilog/SystemVerilog, a Tiny Tapeout-ready export, and the full sim → synth → layout loop.
10. **Analog/RF track:** schematic, SPICE, layout, LVS/DRC, a first oscillator/VCO and LNA, and a TT analog-tile export. Honest documentation of what will not fit on open PDKs or TT.
11. **Acoustic track:** open simulation of SAW/BAW-style resonators and filters, with no claim of fabrication.

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
 ┌────────────────┐                          ┌────────────────────────────────────────┐
 │ AI agent       │  MCP over stdio ───────▶ │ siliconsmith MCP server (Python, host) │
 │ Claude Code /  │  ◀─────── JSON results   │ - project files + path checks          │
 │ Cursor / Codex │           + file paths   │ - result parsers -> JSON               │
 └────────────────┘                          │ - validation ledger + status           │
                                             │ - skills (instructions, explain)       │
                                             │ - container runner                     │
                                             └────────────────────────────────────────┘
                                                                 │ docker run, one container per call
                          ┌──────────────────────────────────────┴──────────┐
                          ▼                                                 ▼
 ┌────────────────────────────────────────────────┐  ┌────────────────────────────────────────────┐
 │ siliconsmith-toolchain image (ours)            │  │ LibreLane image, used as is (M1+)          │
 │ linux/amd64 only for now                       │  │ ghcr.io/librelane/librelane:3.0.14         │
 │ Digital (M0): iverilog, verilator, yosys,      │  │   pinned by digest                         │
 │   cocotb, amaranth, myhdl, pywellen, worker    │  │ yosys, openroad, magic, klayout, netgen    │
 │ Analog (A0+): xschem, ngspice, magic,          │  │ sky130A PDK in a ciel Docker volume        │
 │   klayout, netgen, openEMS                     │  └────────────────────────────────────────────┘
 │ Acoustic (S1+): elmerfem, openEMS, ngspice     │
 └────────────────────────────────────────────────┘
```

### 5.1 Threat model and sandbox

**What runs where.** The MCP server runs on the host and talks to the agent over stdio. It never imports Amaranth, MyHDL or cocotb. Elaborating an Amaranth or MyHDL design and running cocotb tests both execute Python that the agent wrote, so every such step runs in a fresh container: one `docker run` per tool call (`python -m siliconsmith.worker <verb>`), with the request as JSON on stdin. The worker writes exactly one result file at a path the host chose under `build/`. The host reads it with a 1 MiB cap and validates it, because anything that comes out of the container is untrusted. Container stdout and stderr are captured with byte caps and never parsed as results. There is no generic shell tool.

**Container settings (summary).** Every call uses the same fixed argument list (a Python list, no shell):

- no network (`--network none`), a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`;
- limits: 512 processes, 4 GiB memory with no extra swap, 2 CPUs, 2 GiB maximum file size, 4096 open files;
- a 1 GiB `/tmp` in memory; `HOME` and caches point at `/tmp` or `build/`;
- runs as the host user's uid:gid (left out under rootless Docker, which already maps to the user); `--pull never`;
- on timeout the host runs `docker kill` on the named container.

**Mounts.** The project's `hdl/`, `src/`, `test/`, `spec.yaml` and `info.yaml` are mounted read-only. Only `build/` is writable. The worker code is mounted read-only from a per-version cache. Project paths that contain `,`, `:` or a newline are rejected. The Docker socket is never mounted.

**Host file access.** Every host-side read and write goes through one function. It rejects absolute paths, `..`, NUL bytes and backslashes, opens each path component without following symlinks (`O_NOFOLLOW`), accepts only regular files with a single hard link, applies size caps, writes atomically, and locks the project while it works. A symlink or hard link that container code plants under `build/` therefore cannot make the host read or overwrite a file outside the project. Write tools have a per-front-end allow-list (section 5.3). The project root comes from `--root`, then `SILICONSMITH_ROOT`, then `CLAUDE_PROJECT_DIR`, then the current directory; `/` and `$HOME` are refused unless given explicitly.

**What this protects, and what it does not.**

- It protects the host from code that **our tools execute**. A buggy or hostile design or test cannot reach the network, read files outside the project, talk to the Docker daemon, or plant files that the host later runs (git hooks, CI workflows, editor settings, Makefiles): the container can write only `build/`, and our write tools refuse those paths.
- It does **not** protect against an agent that has its own shell or file tools (for example Claude Code's Bash tool). Such an agent can already do anything the user can. The sandbox only covers what runs through Siliconsmith.
- It does not defend against Docker or Linux kernel escapes.
- Whoever can call the server's tools can start containers, so only the local user may reach the server. M0 is stdio only. Streamable HTTP, with Origin checks, DNS-rebinding protection and authentication, is deferred to M5-3.
- LibreLane (M1 onward) runs the same way, from its own image with our flags. We never use `librelane --dockerized`, which mounts the whole `$HOME` read-write.

**Result shape.** Every tool returns the same five fields: `{ok, summary, details, artifacts[], next_hints[]}`.

- `ok=false` means a **design** failure only: a failing or timed-out test, a lint error, a syntax or elaboration error.
- Usage, toolchain and infrastructure problems (bad argument, missing image, container timeout) raise an MCP `ToolError` whose message names the fix, for example `docker pull <image>`.
- `summary` is at most 20 lines and 2,000 characters. Longer output ends with `… (N more lines in details)`.
- `details` is typed per tool (for example lint findings, or per-test results with expected and actual values). `artifacts` are project-relative paths. Container paths (`/work/...`) in messages are mapped back to project-relative paths.

### 5.2 Digital front-end interface

**Project layout.** Each project is a folder `<root>/<name>/` shaped like the Tiny Tapeout Verilog template, so the same files serve simulation now and TT export in M3:

```
siliconsmith.toml  name and front-end; written once by create_project; agents cannot write it
spec.yaml          the only source of clock_hz, pins and requirements (section 5.7)
info.yaml          TT yaml_version 6, written with yaml.safe_dump at create time
hdl/<name>.py      Amaranth or MyHDL source (the entry file; helpers may sit beside it)
src/<name>.v       the core: hand-written (Verilog) or generated ("generated, do not edit")
src/project.v      the tt_um_<name> wrapper, generated and owned by the tool
src/config.json    TT template; CLOCK_PERIOD is generated from spec.yaml from M1-1
test/tb.v, test/Makefile, test/requirements.txt
                   TT template files, patched only for the instance and source names
test/test.py       cocotb 2.x tests; each docstring says which requirements it covers
docs/info.md       TT template; docs/verification.md is generated (section 5.7)
build/             all build output (the only folder the container can write)
.siliconsmith/     ledger.json, written only by the host (section 5.7)
```

The TT template files come from a pinned snapshot of `ttsky-verilog-template` shipped inside the package, with a manifest of our patches. Generated files carry an Apache-2.0 SPDX header and a copyright line with the author given to `create_project`. Generated output is reproducible: the same inputs give byte-identical files, with no `/home` or `/work` paths in them.

**Core/wrapper contract.** The agent writes only the **core** module `<name>`. Its ports use TT pin names, and it may use any subset of them: inputs `clk`, `rst_n` (active-low, synchronous reset), `ena`, `ui_in[7:0]`, `uio_in[7:0]`; outputs `uo_out[7:0]`, `uio_out[7:0]`, `uio_oe[7:0]`. The tool generates `src/project.v`, the `tt_um_<name>` wrapper with TT's fixed eight ports (checked against [ttsky-verilog-template/src/project.v](https://github.com/TinyTapeout/ttsky-verilog-template/blob/main/src/project.v)). The wrapper connects the core by name, ties unused outputs to 0, and lists unused inputs in an `_unused` wire, as the template does. Ports are read back with Yosys (`write_json`) and checked for names, directions and widths, so the agent cannot break the TT pin contract.

**Front-ends.** All emission runs inside the container (section 5.1).

- **Amaranth** (0.5.10). `hdl/<name>.py` defines a module-level `def top() -> wiring.Component` whose signature uses TT pin names. The design creates its own `sync` clock domain and drives it from `clk` and `~rst_n`, because Amaranth's default reset is an active-high `rst` that TT does not have. The worker calls `verilog.convert(top(), name=<name>, emit_src=False)`; `emit_src=False` keeps absolute file paths out of the output. Amaranth uses its bundled Yosys (`amaranth-yosys`, selected with `AMARANTH_USE_YOSYS=builtin`), so a different Yosys on `PATH` cannot change the output.
- **MyHDL** (0.11.52). `hdl/<name>.py` defines `@block def <name>(...)` whose parameters are TT pin names. The worker creates the signals (for `rst_n`, `ResetSignal(0, active=0, isasync=False)`) and calls `convert(hdl='Verilog', name=<name>, testbench=False, no_myhdl_header=True, timescale='1ns/1ps')`. A `ToVerilogWarning` (for example an output that is never driven) is treated as an error.
- **Verilog/SystemVerilog.** `src/<name>.v` passes straight through.

After emission the tool regenerates the wrapper and lints the whole design with `verilator --lint-only -Wall --timing` (latches are errors; for Amaranth cores the WIDTHEXPAND/WIDTHTRUNC warnings are off, because Amaranth sets widths itself and its Verilog writer emits minimal-width constants), then runs Yosys `check -assert` for what Verilator misses (multiple drivers, logic loops, undriven wires). Both are errors. Lint findings come back as `{severity, code, file, line, col, msg}`; a lint failure is a design result (`ok=false`), not a tool error. Python errors are mapped to a file and line under `hdl/`. A missing `top()` or `@block` function is a design error that names the file. The agent's Python runs in a child process that is killed, with everything it started, before Yosys and Verilator run. It still shares the container and the user id with the worker, so a hostile design could kill the worker and write a result file itself: the lint verdict is only as trustworthy as the design code. The host re-checks the ports, regenerates the wrapper and inspects the core, but cannot re-run the lint.

### 5.3 Digital MCP tools

The server uses the official MCP Python SDK v2 (`mcp>=2.3,<3`). One module per tool, one registration list. The result shape is in section 5.1.

| Tool | Inputs | Outputs | From |
|---|---|---|---|
| `toolchain_info` | none | Readiness: Docker reachable, rootless or not, image present (if missing, `next_hints` gives the `docker pull` command). Image id and registry digest (null for a local build). Versions of iverilog, verilator, yosys, amaranth, amaranth-yosys, myhdl, cocotb, Python and pywellen. `pdk` is null until M1. | M0 |
| `create_project` | `name`, `frontend` (amaranth\|myhdl\|verilog; the server's `--default-frontend` sets the default), `author`, `description` | A TT-shaped project (section 5.2) with a `spec.yaml` stub, `info.yaml`, the generated wrapper and a cocotb test stub. Tile size defaults to 1x1 in `info.yaml`; the PDK is sky130A until M5. | M0 |
| `list_files` / `read_file` | `path`, optional line range | File contents, size-capped, project dir only | M0 |
| `write_spec` | full `spec.yaml` content | Validation errors with the field path, 20 lines or fewer | M0 |
| `write_rtl` | `path`, full `content` (no diffs or patches) | Writes the file. A `.py` file gets a host-side syntax check (`ast.parse`; never executed) and is saved even if the check fails. | M0 |
| `write_test` | `path`, `content` | Saves a cocotb 2.x test under `test/` | M0 |
| `emit_verilog` | none | Generated core path, top module, port list, lint findings. Its run is the `check` step of the validation sequence. | M0 |
| `run_tests` | `simulator` (icarus\|verilator), `test_filter`, `waves` (bool); `gl` (gate-level) is added in M1 | Re-emits, lints and simulates in one container call. Pass/fail per test; the first failing assertion with file, line, expected and actual value; wave file path if requested. | M0 |
| `inspect_waves` | wave file path, `signals[]`, `time_range`, `max_rows` (default 50) | Compact value table, read with pywellen (VCD or FST, detected from the file's first bytes) | M0 |
| `status` | none | The validation sequence for the project (section 5.7); also writes `docs/verification.md` | M0 |
| `explain` | `topic` (e.g. "setup violation", "latch inferred") | Short beginner explanation from the bundled skills and glossary | M0 |
| `synthesize` | `target` (generic\|sky130), `flatten` | Cell count, area µm², latches found, multi-driven nets, combinational loops, utilization estimate vs the tile budget | M1 |
| `run_layout` | `stage` (full\|floorplan\|place\|cts\|route\|signoff), `config_overrides` | Run id, status, failing step, WNS/TNS per corner, DRC/LVS/antenna counts, utilization, GDS path. Long runs return a job handle (M2-5). | M2 |
| `read_report` | `run_id`, `kind` (timing\|drc\|lvs\|antenna\|area\|power\|log), `top_n` | Parsed worst items (e.g. 5 worst timing paths with start/end points) | M2 |
| `render_layout` | `run_id`, `format` (png\|svg) | KLayout-rendered image path (CLI only; KLayout is GPL) | M2 |
| `export_tt` | none (title, author, description, pins and clock come from `create_project` and `spec.yaml`) | A TT-format repo (`info.yaml`, `src/`, `test/`, `docs/info.md`, `.github/workflows`) written to a fixed export directory inside the project, never to an agent-chosen `repo_dir` | M3 |
| `tt_precheck` | none | Result of TT's precheck on the export directory *(M3-2 decides: local run in TT's own environment, or CI only)* | M3 |

**Write allow-list.** Verilog projects may write `src/<name>.v`; Amaranth and MyHDL projects may write `hdl/**/*.py`. All projects may write `spec.yaml`, `test/**/*.py` (not `conftest.py`) and `docs/**/*.md`, except the generated `docs/verification.md`. Dotfiles, Makefiles and `*.mk` files are never writable. A rejected write names the file the agent should use instead. No tool can approve the spec (section 5.7).

**Simulation details.** `run_tests` uses the cocotb 2.1 Python runner with `tb` as the top level and a 1 ns / 1 ps timescale. Verilator gets `--timing` (TT's `tb.v` contains a delay) and `--trace-fst` when waves are requested.

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

### 5.6 Skills (playbooks shipped with the package)

Each skill is a `SKILL.md` file (YAML front matter with `name` and `description`, then plain text) shipped as package data under `src/siliconsmith/skills/<name>/SKILL.md`. The server delivers them two ways, so any MCP client gets them:

- its MCP `instructions` (at most 4 KB: the loop in short form and a pointer to `explain`);
- the `explain(topic)` tool, which returns skill sections and glossary entries.

A Claude Code plugin, a `siliconsmith install-skills` command and MCP prompts/resources come later (M3-6).

Skills in M0:

- `chip-design-loop`: spec → tests first → RTL → `emit_verilog` → `run_tests` → `status`, with stop rules (a maximum number of iterations; stop and ask the user when a requirement is unclear). In a headless run with nobody to ask, the agent writes its assumptions to `open_questions` in `spec.yaml` and continues. Agents never run `siliconsmith approve`.
- `tt-rules`: TT pin interface, `clock_hz`, tile sizes, "assign all outputs", 66 MHz IO limit ([TT clock spec](https://tinytapeout.com/specs/clock/)).
- `amaranth-style`, `myhdl-style`: beginner patterns (counter, FSM, shift register) and common mistakes (for example: in Amaranth, drive the `sync` domain from `clk` and `rst_n`; in MyHDL, use `modbv` for counters that wrap).
- `cocotb2-tests`: cocotb 2.x test idioms (`Clock(dut.clk, 20, unit="ns")`, `ClockCycles`, `int(dut.uo_out.value)`, `Covers:` docstrings) and a list of cocotb 1.x idioms that no longer work (`BinaryValue`, `.integer`, `cocotb.fork`, `units=`).

Later skills:

- `debug-timing`, `debug-drc` (M2-4): how to read the parsed reports and the usual fixes (pipeline a stage, lower the clock, raise tiles, adjust `PL_TARGET_DENSITY_PCT`).
- `analog-reality`: section 8 of this plan, so agents give honest answers.
- `analog-flow`: Xschem → ngspice → Magic/KLayout → netgen, TT analog pins and templates.
- `acoustic-reality`: section 9; sim only, no fab, and what each solver can and cannot do.

### 5.7 Validation sequence

The validation sequence is the list of checks between an idea and a working chip. The tool records which checks have passed **for the current files**, so a fresh agent session, or the owner, can see what has been proven and what has not. It is defined once and used by every track; M0 builds the digital steps.

**Spec.** `spec.yaml` (version 1) is the single source of the clock, pins and requirements:

```
title, summary, clock_hz, reset
pins:            {ui: {<bit>: <meaning>}, uo: {...}, uio: {...}}
requirements:    [{id: R<n>, text, limit: {min, typ, max, unit}}]    (limit is optional)
open_questions:  [...]
```

`write_spec` validates it and reports errors with the field path. `create_project` writes a valid stub. The optional `limit` is a datasheet-style min/typ/max, meant mainly for the analog and acoustic tracks.

**Coverage tags.** Each cocotb test says in its docstring which requirements it checks, for example `Covers: R1, R2`. The tool reads these tags with Python's `ast` module; it never runs the test file to find them. An unknown requirement id is a warning. A skipped test does not count as coverage.

**Ledger.** `.siliconsmith/ledger.json` in the project folder stores facts only: the spec approval (spec hash, who, when) and one record per `emit_verilog` or `run_tests` run (tool, simulator, the check, sim and spec digests, image name and tag, image id when known, whether a test filter was used, time, `ok`, and for each test its name, the requirement ids it covers and its outcome). Only the host writes it (the container never sees it), and only `emit_verilog`, `run_tests` and the `approve` command add to it.

**Derived status.** `status` works out each step's state when it is asked, from the ledger and the current files: `pass`, `fail`, `stale` (an input changed since the last run), `not_run`, or `not_available` (the step belongs to a later milestone). Each step has its own inputs:

- approval ← `spec.yaml`;
- check ← `hdl/**/*.py` (Amaranth, MyHDL) or `src/<name>.v` (Verilog), `info.yaml`, `test/tb.v` and `test/Makefile`, the toolchain image and the Siliconsmith version (the version fixes the files the tool owns: the generated wrapper and the TT template files). The image is identified by its name and tag, which is content-addressed (`ctx-<hash>`), so status never needs Docker; the ledger also records the image id when it is known;
- simulation ← the check inputs plus `test/**/*.py`;
- coverage ← the spec plus the latest unfiltered simulation runs.

These file patterns match the write allow-list (section 5.3), so every design or test file an agent can write is an input to some step. The digest is a sha256 over the image name and tag, the Siliconsmith version and the files those patterns match (path and content hash of each), never whole folders, so build output, `__pycache__` and wave files do not affect it. The write allow-list rejects any path with a `build`, `sim_build` or `__pycache__` component, so nothing an agent can write is skipped. A run's digests are taken before the run starts, so a file edited during the run shows as stale, never as tested.

**Steps for the digital track.** The steps are stored as data per track, so analog and acoustic add their own later.

1. Spec valid.
2. Spec approved by the owner.
3. Check: Verilog generated and lint clean.
4. RTL simulation on Icarus passes.
5. RTL simulation on Verilator passes.
6. Requirement coverage: every requirement id is covered by a test that passes on both simulators at the current digest.
7. Synthesis and gate-level simulation (M1).
8. Layout signoff (M2).
9. TT precheck and TT's own CI (M3).
10. Silicon bring-up on the TT demo board (shown as `not_available` with its milestone).

A run with a test filter, or with zero tests, never sets a simulation or coverage step.

The check step (3) is set by the latest `emit_verilog` **or** `run_tests` run that reached the lint stage, because `run_tests` re-emits and lints in the same container call. A run that passed the lint stage and then failed in the simulator still counts as a clean check. Each ledger run also stores the `spec.yaml` hash, so that editing the spec makes the coverage step stale (coverage depends on the spec as well as on the simulation runs).

**Approval is the owner's step.** `siliconsmith approve <dir>` is a command-line command only; there is no MCP tool for it. It asks for confirmation on a terminal and records the git `user.name` and the spec hash. This is an **honest-use convention, not a security boundary**: an agent with its own shell could run the command. The docs and the `chip-design-loop` skill say so, and the skill forbids agents from running it.

**Report.** The `status` tool and `siliconsmith status <dir>` write the same `docs/verification.md`, a table with the columns Step, What it proves (plain English), Status, Evidence and Date. Agents cannot write that file.

**Held-out reference test.** M0 adds a frozen reference test for the demo counter under `tests/reference/` that the agent never sees. It assumes only the prompt: +1 per clock while `ui_in[0]` is 1, hold while it is 0, wrap from 255 to 0, and no particular reset value. CI and the recorded demo run it on both simulators.

## 6. Docker toolchain

- **Our image.** `ghcr.io/rfingadam/siliconsmith-toolchain`, built from `docker/Dockerfile`, `linux/amd64` only for now (arm64 is M5-3). Tags are `ctx-<h>`, where `<h>` is a 12-hex-digit hash of the files in `docker/`, plus a date tag `<YYYYMMDD>`. The server's default image is the `ctx-` tag that matches the `docker/` folder it was released with. M0 ships a simulation-only image.
- **Pins for M0** (digests are re-resolved when the Dockerfile is written):

  | Item | Pin | Why |
  |---|---|---|
  | Base | `ubuntu:24.04` by digest; apt from a dated Ubuntu snapshot | Reproducible builds |
  | Icarus Verilog | apt `iverilog=12.0-2build2` | cocotb 2.1 needs Icarus 11.0 or newer; TT's RTL test workflow also uses apt 12.0 |
  | Verilator | v5.046, copied from the official `verilator/verilator:v5.046` image (by digest) | cocotb 2.1 needs 5.036 or newer and its CI tests 5.046; Ubuntu 24.04's apt Verilator is 5.020 |
  | Yosys (M0) | `yowasp-yosys==0.62.0.0.post1088` (Yosys 0.62 compiled to WebAssembly) behind a `/usr/local/bin/yosys` shim | Same Yosys version as LibreLane 3.0.14; TT's own tools also use yowasp-yosys; apt Yosys is 0.33. A native Yosys build is decided in M1-0. |
  | cocotb, pytest | `cocotb==2.1.0`, `pytest==8.4.2` | Same as TT's template `test/requirements.txt` |
  | Amaranth | `amaranth[builtin-yosys]==0.5.10`, with `AMARANTH_USE_YOSYS=builtin` | Amaranth rejects a system Yosys older than 0.40 (apt has 0.33). With `AMARANTH_USE_YOSYS=builtin` it always uses its bundled Yosys 0.50, so a Yosys on `PATH` cannot change its output. |
  | MyHDL | `myhdl==0.11.52` | Supports Python 3.12 to 3.14 |
  | Waveforms | `pywellen==0.25.7` | Reads VCD and FST from both simulators |
  | Other apt packages | `python3 python3-venv libpython3.12t64 g++ make perl ccache zlib1g-dev ca-certificates` | cocotb needs libpython; Verilator compiles C++ models (ccache is required because the official Verilator build used it) |
  | TT template | `ttsky-verilog-template` commit `a42e6662` (2026-10-14), shipped in `src/siliconsmith/templates/tt` with an `UPSTREAM` manifest | The template has no tags |

  Python packages go into `/opt/venv` from a hash-locked `docker/requirements.lock`.
- **Host package.** The MCP server is a small Python package, installed with pip from a checkout (`pip install -e .`; there is no PyPI release yet), with `mcp>=2.3,<3`, `pyyaml` and `pydantic>=2`. The extras `[amaranth]` and `[myhdl]` are only for writing designs with editor support; all execution happens in the image. A `--native` mode that uses tools already on `PATH` is deferred and has no milestone yet.
- **LibreLane and the PDK (M1-0).** LibreLane runs from its official image, `ghcr.io/librelane/librelane:3.0.14`, referenced by digest and started directly with our container flags (section 5.1), never with `--dockerized`. We do not rebuild or re-publish that image. LibreLane is never a Python dependency of our package (it would pull in the GPL `klayout` Python module). The sky130 PDK lives in a Docker volume filled by `ciel` ([fossi-foundation/ciel](https://github.com/fossi-foundation/ciel), Apache-2.0) at the version LibreLane 3.0.14 uses. The PDK hashes TT uses are recorded in `tt.lock` (section 16).
- **Analog and acoustic tools** (Xschem, ngspice, Magic, netgen, openEMS, Elmer) are added to the image in A0 / S1, not in digital M0.
- **Licensing.** We don't modify any GPL/LGPL tool, and the repo contains no tool binaries or sources. Our image does distribute binaries, so it ships `/usr/share/doc/siliconsmith-toolchain/` with `SOURCES.txt` (the source package and version of every Ubuntu package; the upstream URL at the exact tag or version for Verilator v5.046 and every pip package), `THIRD_PARTY_LICENSES.md`, a written offer for the corresponding source, and the license files of bundled components (wellen, Yosys, ABC, yosys-slang, slang). A hosted source bundle per image is M5-3 (section 10).
- **Size.** The sim-only image size is recorded in the README (M0-2). The PDK stays out of the image: the default sky130 library set is about 338 MB to download and 2.1 GB unpacked, and `sky130_fd_sc_hd` alone is about 134 MB and 752 MB. The LibreLane amd64 image is about 1.5 GB compressed.

## 7. Repo layout (target)

```
siliconsmith/
├─ LICENSE                      # Apache-2.0
├─ LICENSES/Apache-2.0.txt      # license text where the REUSE tool expects it
├─ REUSE.toml                   # license and copyright for files without headers
├─ NOTICE, CONTRIBUTING.md
├─ THIRD_PARTY_LICENSES.md
├─ README.md, CLAUDE.md, PLAN.md, ISSUES.md
├─ pyproject.toml, uv.lock      # package siliconsmith; extras: [amaranth], [myhdl]
├─ src/siliconsmith/
│  ├─ server.py                 # MCP server (official MCP Python SDK v2, MIT)
│  ├─ cli.py                    # command line: server, status, approve
│  ├─ result.py, paths.py, project.py, scaffold.py, spec.py, ledger.py
│  ├─ frontends/                # amaranth, myhdl, verilog
│  ├─ runners/                  # container runner (docker run)
│  ├─ worker/                   # code that runs inside the container
│  ├─ tools/                    # one module per MCP tool
│  ├─ parsers/                  # lint, results.xml, later synth/layout reports
│  ├─ skills/<name>/SKILL.md    # skills, shipped as package data
│  └─ templates/tt/             # pinned TT template snapshot + UPSTREAM manifest
├─ scripts/                     # helper scripts for development and CI
├─ docker/                      # Dockerfile, requirements.lock
├─ tests/                       # unit and integration tests; fixtures/, golden/, reference/
├─ docs/                        # beginner guide, glossary, demos/
├─ examples/                    # later (M4 onward); see the example tables below
│  ├─ digital/   00_counter_amaranth/ … 05_dds_nco/
│  ├─ analog/    00_inverter/  01_ring_osc/  02_vco/  03_lna/
│  └─ acoustic/  00_bvd_resonator/  01_ladder_filter/
└─ .github/workflows/           # ci.yml, image.yml; nightly-layout.yml (M2), tt-compat.yml (M3)
```

Folders appear when the issue that needs them lands.

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

1. GPL tools (Icarus, KLayout, Xschem, netgen, openEMS, Elmer, OpenSTA inside OpenROAD, and any other GPL tool we add) are only ever called as **separate processes** (subprocess/docker). Never import, link, or copy their source.
2. LGPL (MyHDL, Verilator) is fine as an unmodified dependency or external binary. Keep MyHDL an *optional extra* (`pip install -e .[myhdl]` from a checkout).
3. The repo contains no tool source or binaries. The Docker image is a separate distribution, and it contains GPL and LGPL binaries, so **each published image carries its corresponding-source information**: `/usr/share/doc/siliconsmith-toolchain/SOURCES.txt` (the source package and version of every Ubuntu package, which identifies the exact source in the Ubuntu archive; the upstream URL at the exact tag or version for Verilator and every pip package), `THIRD_PARTY_LICENSES.md`, and a written offer to provide the complete corresponding source. The written offer covers the GPL-2.0 components. For the GPL-3.0 components (for example gcc, make, ccache, binutils), `SOURCES.txt` is the "clear directions" that GPLv3 section 6(d) requires for a download, and we remain responsible for that source staying available. We distribute Verilator under its Artistic-2.0 option. A hosted source bundle per image is planned (M5-3) as the backstop. Images we only reference by digest and never re-publish, such as LibreLane's, are distributed by their own projects.
4. Example designs and generated TT repos are Apache-2.0 with SPDX headers (`// SPDX-License-Identifier: Apache-2.0`), matching the TT template.
5. **DCO for outside contributors:** they add `Signed-off-by` to their own commits (Developer Certificate of Origin, lighter than a CLA). AI agents never add `Signed-off-by` lines (not even the owner's), `Co-authored-by` lines, "Generated with" lines, or any other bot credit. The CI trailer scan fails on `Co-authored-by`, "Generated with" and AI-tool trailers in any commit or the PR body. It fails on `Signed-off-by` only in PRs from a branch of this repository (the owner's and agents' PRs). In PRs from a fork, outside contributors' own `Signed-off-by` lines pass.
6. Do not copy code from unlicensed repos, including mcp4eda.

*Not legal advice.* This follows standard open-source practice, and it is fine for a hobby project.

## 11. Digital milestones and acceptance criteria

Full issue text is in `ISSUES.md`.

| M | Scope | Acceptance criteria |
|---|---|---|
| **M0** Hello-world sim | Repo skeleton and REUSE licensing; sim-only Docker image; MCP server (stdio) with `toolchain_info`, `create_project`, `list_files`, `read_file`, `write_spec`, `write_rtl`, `write_test`, `emit_verilog`, `run_tests`, `inspect_waves`, `status`, `explain`; Amaranth + MyHDL + Verilog front-ends; spec capture (`spec.yaml`, M0-7) and the validation ledger (M0-8); 5 skills | **Deterministic CI end-to-end test:** for each front-end, a known counter goes through the MCP tools and passes its cocotb tests and the held-out reference test on Icarus **and** Verilator (6 cells). **Recorded demo:** Claude Code (headless), starting from an empty folder and given only "8-bit counter on uo_out, count enable on ui_in[0]", produces RTL in **each** front-end whose own tests **and** the held-out reference test pass on Icarus **and** Verilator, with no human edits; loop iterations are recorded. Failing tests return the first failing assertion in ≤20 lines. `status` shows every automated M0 step as pass. CI green (`ci.yml` and `image.yml`). Per-issue criteria and the test that proves each one are in `ISSUES.md`. |
| **M1** Synthesis | M1-0: LibreLane 3.0.14 image by digest, sky130 PDK in a ciel volume, `tt.lock`, native Yosys decision, `run_tests(gl)` plumbing. `synthesize` (sky130_fd_sc_hd, mirroring LibreLane's synthesis script), latch/loop/multi-driver detection, tile-budget estimate; gate-level sim; M1-3 EQY equivalence check | The counter synthesizes to a sky130_fd_sc_hd netlist and reports cell count and area. A seeded latch bug is reported with its file and line for Verilog and MyHDL (Amaranth cannot infer latches, so it is not applicable there). Gate-level sim (`run_tests gl=true`) passes on Icarus against the Yosys netlist, with the cell models in `FUNCTIONAL` mode and `UNIT_DELAY=#1`. EQY proves the RTL equivalent to the netlist. The final criteria are set in the M1 plan-mode pass. |
| **M2** Layout/GDS | `run_layout`, `read_report`, `render_layout` via LibreLane on sky130A, at TT 1x1 dimensions; debug skills (M2-4); job handles and progress for long runs (M2-5) | The counter produces a GDS with 0 DRC, 0 LVS and 0 antenna errors (or antenna results reported clearly), and setup and hold slack ≥ 0 at 50 MHz in every STA corner LibreLane reports for the TT config. A seeded oversize design (two 64-bit shift registers loaded serially from `ui_in`, a registered 64×64 product, XOR-reduced to `uo_out`, in a 1x1 tile) reports a utilization failure with a suggested fix. The PNG renders. Runtime is measured on the GitHub `ubuntu-24.04` runner (laptop numbers are informational). |
| **M3** Tiny Tapeout ready | `export_tt`, `tt_precheck`, docs generation from `spec.yaml`; automated `tt-compat` gate (M3-4); demo-board bring-up test and shuttle guide (M3-5); distribution: Claude Code plugin, `install-skills`, MCP prompts/resources (M3-6) | An automated CI gate (`tt-compat.yml`) runs the exported counter repo through TT's `gds`, `precheck` and `gl_test` actions (workflow verified in [ttsky-verilog-template](https://github.com/TinyTapeout/ttsky-verilog-template/blob/main/.github/workflows/gds.yaml)), with versions pinned in `tt.lock`. The owner also pushes the export to a test GitHub repo by hand once, to confirm. `info.yaml` pinout is filled in from `spec.yaml`, and `docs/info.md` explains how to test. No purchase is made automatically. |
| **M4** Stretch examples | UART TX/RX, SPI peripheral, PWM/servo, NCO/DDS (an RF-flavoured digital block), tiny RISC-V (RV32E subset, multi-tile); mutation / weak-test check (M4-6) | Each example passes M0 through M3 checks in CI (layout nightly). The RISC-V example runs a small program in cocotb. A benchmark script reports the agent's success rate across about 10 prompts. |
| **M5** (optional) More PDKs | `pdk=gf180mcuD`, `pdk=ihp-sg13g2` (TT has `ttgf-*` and `ttihp-*` templates); docs that point at the analog/RF track; infra follow-ups (M5-3: streamable HTTP, arm64 image, hosted source bundle) | The counter hardens on at least one extra PDK. Digital skills point at the analog track instead of pretending analog is "docs only." |

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

All workflows run on `ubuntu-24.04` (not `ubuntu-latest`). Actions are pinned by commit SHA, and Dependabot keeps them current. Keep every `ci.yml` job green on `main`.

- **`ci.yml`** (every PR and every push to `main`; a newer run cancels an older one; token permissions `contents: read`, `packages: read`):
  - `lint`: ruff check and format; mypy (strict); `reuse lint` (reuse 6.3.0 with charset-normalizer, via `uvx`); a stale-API guard on `src/**/*.py` and `src/siliconsmith/templates/**` that fails on `FastMCP`, `BinaryValue`, `cocotb.runner` and `units=` (skill text is not scanned, because the `cocotb2-tests` skill names these on purpose); a no-binaries check (ELF files, archives, or files over 1 MB outside an allow-list); a wheel-contents check (every `SKILL.md` and template is in the wheel); a trailer scan that fails on `Co-authored-by`, "Generated with" and AI-tool trailers in any commit or the PR body, and on `Signed-off-by` only in PRs from a branch of this repository (in PRs from a fork, outside contributors' own `Signed-off-by` lines pass, see section 10 rule 5); and a pip-compat step (Python 3.12 from setup-python, a fresh venv, `pip install -e .[amaranth,myhdl]`, `siliconsmith --version`, `import amaranth, myhdl`).
  - `unit`: Python 3.11 and 3.14, `pytest -m "not integration"` (parsers are tested against saved real logs as fixtures).
  - `integration`: compute the image tag `ctx-<h>` (`python -m siliconsmith._imagetag`), log in to GHCR with the workflow token, and pull that tag; if the pull fails for any reason, build the image in the job (buildx with the GitHub Actions cache). Smoke test: `iverilog -V && verilator --version && yosys -V`, plus a check that Verilator is 5.036 or newer. Then `pytest -m integration`: the 6 front-end × simulator cells as separate test ids, plus emit, lint, error, waves, ledger, `toolchain_info` and TT-Makefile-parity tests.
  - Target: under 10 minutes when `docker/` has not changed *(unverified)*.
- **`image.yml`** (publishes the toolchain image; needs `packages: write`):
  - On a PR that touches `docker/**` or the workflow itself: build and smoke-test, no push.
  - On a push to `main` that touches `docker/**`, or when started by hand: if `ctx-<h>` already exists, do nothing. Otherwise build `linux/amd64`, push `ctx-<h>` and `<YYYYMMDD>`, run one cocotb test per simulator in the image, and write the digest and size to the job summary.
  - No schedule. Rebuilds happen only when `docker/` changes (for example when Dependabot bumps the base image digest).
  - The first push of the package must come from this workflow, so GitHub links the package to this repo. If GitHub creates the package as private, the owner makes it public by hand (Package settings → Change visibility), then checks with an anonymous `docker pull`.
  - The server's `DEFAULT_IMAGE` is `ghcr.io/rfingadam/siliconsmith-toolchain:ctx-<h>`. Unit tests fail if it, or the image size line in the README, does not match `docker/`.
- **`nightly-layout.yml`** (M2): LibreLane hardening of digital examples on sky130A at TT tile sizes, uploads GDS/PNG/metrics as artifacts, and fails if DRC/LVS/timing regress.
- **`tt-compat.yml`** (M3-4): runs the exported counter repo through `TinyTapeout/tt-gds-action` (`gds`, `precheck`, `gl_test`; the same actions TT uses). TT moves its shuttle tags (`ttsky26d` and so on), and the action checks out `tt-support-tools` at `main` unless told otherwise, so the run uses the tag and the commit recorded in `tt.lock`.
- **Agent benchmark** (manual workflow): runs a fixed prompt set against a chosen model and records pass rate and iterations. It needs an API key secret and **only runs when triggered by hand** to control cost.

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

- **Shuttles** on 2026-11-11 (from the [tinytapeout.com](https://tinytapeout.com/) site source and the TT app's shuttle API, `app.tinytapeout.com/api/shuttles/<id>`):
  - **SKY26d** (sky130): sold out. 0 of 512 tiles and 0 of 66 devkit boards left.
  - **SKY26e** (sky130): closes **2026-11-30 20:00 UTC**. 362 of 512 tiles were free, but **no devkit boards are left** (0 of 14).
  - **GF26c** (GF180): closes **2026-12-07 20:00 UTC**, with 73 of 160 tiles and 181 of 200 devkit boards left.
  - On 2026-11-11 tt-gds-action had no `ttsky27*` tag, and we found no announced sky130 shuttle after SKY26e (check [tinytapeout.com](https://tinytapeout.com/) before deciding). Picking a shuttle is an owner decision before M3. Plan for a 2027 shuttle if a 2026 close is too soon.
- **Cost (official calculator and analog specs, 2026-11-11):** the [Tiny Tapeout calculator](https://app.tinytapeout.com/calculator?pcbs=1&tiles=1) at default view showed **€70 per digital tile**, **€300 per devkit PCB**, and **€15** worldwide economy shipping, totalling **€385** for 1 tile + 1 devkit + shipping. Early-bird pricing is limited and only for individuals ([tinytapeout.com](https://tinytapeout.com/)); the calculator has shuttle / identity / design-type controls that can change the total, so re-check it before any order.
- **Analog (sky130), from the official [analog specs](https://tinytapeout.com/specs/analog/):** projects with analog pins must be two tiles high, so the minimum area cost is €140 (2 × €70). Analog pins cost **€40 per pin for the first 2 pins** on a shuttle (per customer) and **€100 per pin** after that. Example on that page: 1x2 + 2 pins = €220 before ASIC, PCB, or shipping. Analog slots are limited per shuttle.
- **Size:** a 1x1 digital tile is **161.00 × 111.52 µm** (`DIE_AREA` in tt-support-tools [tech/sky130A/tile_sizes.yaml](https://github.com/TinyTapeout/tt-support-tools/blob/main/tech/sky130A/tile_sizes.yaml); the template comment says about 167×108 µm and the [FAQ](https://www.tinytapeout.com/faq/) about 160×100 µm and roughly 1,000 gates). Our tile-budget estimate uses `tile_sizes.yaml`. Valid digital sizes include 1x1, 1x2, 2x2, 3x2, 4x2, 6x2, 8x2 (template `info.yaml`); `tile_sizes.yaml` also accepts 3x4, 4x4, 5x4, 6x4 and 8x4. 1x2 is 161.00 × 225.76 µm and 2x2 is 334.88 × 225.76 µm; analog templates use those two sizes.
- **Languages:** the `language` field in TT's `info.yaml` is free text. tt-support-tools only checks that it is not empty and treats only "Wokwi" specially, so "Amaranth" and "MyHDL" are accepted. TT's tools read only the Verilog files listed in `source_files` (VHDL is translated first), so for Amaranth and MyHDL the generated Verilog must be committed in `src/`. TT has no official Amaranth or MyHDL template; VHDL templates exist.
- **Tool versions TT uses:** at `ttsky26d` and `ttsky26e`, tt-gds-action installs LibreLane 3.0.14 and runs it with `--dockerized`. TT's flow uses three different sky130 PDK versions: hardening and gate-level tests use open_pdks `8afc8346…` (from LibreLane 3.0.14), precheck uses `0536d02d…`, and TT's separate equivalence-check action pins a third (`cd1748bb…`). TT's shuttle tags are moved over time, and tt-gds-action checks out tt-support-tools at `main` by default. M1-0 records all of these (shuttle tag, tt-support-tools commit, LibreLane version, PDK hash per step) in `tt.lock`.
- You get **one chip** per devkit. More chips require buying more devkits.
- Never auto-order. Prices and slots change; always re-check the calculator.

## 17. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Toolchain churn (Efabless shutdown → LibreLane; TT bumps LibreLane version per shuttle and moves its tags) | Flows break | Pin to the LibreLane version TT's action uses and record TT's versions in `tt.lock` (M1-0), nightly CI |
| API drift: MCP Python SDK v2 (2026-07-28) renamed `FastMCP` to `MCPServer`; cocotb 2.x removed `BinaryValue`, moved the runner to `cocotb_tools.runner` and renamed `units=` to `unit=`. Most model training data shows the old APIs. | Our code, or tests the agent writes, fail on import or at run time | Pin `mcp>=2.3,<3` and `cocotb==2.1.0`; API rules in CLAUDE.md; CI stale-API guard; `cocotb2-tests` skill |
| MyHDL has few maintainers and rare releases (0.11.52 in 2026-09, the one before in 2024-12); Verilog output quirks (an output that is never driven is emitted as an input, with only a warning) | Second front-end lags | 0.11.52 supports Python 3.12 to 3.14 and its CI passes on them; the front-end is isolated; `ToVerilogWarning` is treated as an error; MyHDL is marked "beta" if needed |
| Agent-written HDL and tests are code that we execute | Damage to the user's machine | Runs only in the hardened container (section 5.1); the limits of that sandbox are documented |
| LLMs write subtly wrong RTL that still passes weak tests | Bad silicon | Skill requires test-first; requirement coverage and a held-out reference test (M0); gate-level sim and EQY equivalence (M1); mutation check (M4-6); optional formal checks via SymbiYosys (later) |
| LibreLane runtime/RAM on laptops | Slow loop | Run layout only after sim and synth pass; cache PDK; small designs take minutes per TT FAQ |
| Large images and runner disk: GitHub's standard runners guarantee only 14 GB of free disk; the default sky130 library set is about 2.1 GB unpacked and the LibreLane image about 1.5 GB compressed | Setup friction; CI runs out of disk | Sim-only image in M0; PDK in a Docker volume, not the image (only `sky130_fd_sc_hd`, about 752 MB, where that is enough); pin `ubuntu-24.04` runners (the newer runner image's Docker storage uses more disk); free disk space in CI if needed |
| License mistakes (vendoring GPL code, or copying unlicensed code) | Repo relicensing pain | CI check: `reuse lint` / SPDX headers; rules in CLAUDE.md; never copy mcp4eda |
| Scope creep (full Bluetooth radio, SAW fab) | Never ships | Digital M0 first; radio and fab are explicit non-goals; skills state the limits |
| Elmer piezo or openEMS-for-acoustics may not do what we want | Acoustic track stalls | S1 accepts a documented failure; BVD models still land in S0 |
| Agent API cost in CI | Money | Agent benchmark only runs on manual trigger |
| Owner is new to HDL | Can't judge outputs | Beginner docs, explained examples, `explain` tool |
| Tapeout deadlines/prices change | Plan out of date | Always re-check the TT calculator before ordering; never auto-order |

## 18. Effort estimate (rough, unverified)

Digital: M0: about 6–8 agent-days · M1: 2–3 days (including M1-0, the LibreLane runner and PDK volume, and the EQY check) · M2: 2–4 days · M3: 1–2 days · M4: about 1 day per example, RISC-V longer.

Analog: A0: 1–2 days · A1: 2–3 days · A2: several days per target · A3–A4: stretch.

Acoustic: S0: 1 day · S1–S2: research, open-ended.

## 19. Next steps

1. Digital M0 is being implemented as one branch and one pull request per issue, against the GitHub issues (`CLAUDE.md`).
2. Keep CI green, finish M0, then plan M1 in plan mode and continue milestone by milestone.
