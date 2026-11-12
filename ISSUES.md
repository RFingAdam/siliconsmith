# Siliconsmith: GitHub-ready milestone issues

Copy is already filed on the GitHub tracker with the labels shown. Do **not** add `agent-approved`.
Start with digital **M0** only. See `PLAN.md` for architecture, MCP tools, and licensing rules. See `CLAUDE.md` for implementation rules.

---

# Track: digital (`track:digital`)

## [M0-1] Repo skeleton, Apache-2.0 licensing, CI baseline
**Labels:** `track:digital` `milestone:M0` `infra`
**Goal:** Empty, buildable Python package with licensing done right from day one.
**Tasks**
- `pyproject.toml` (hatchling; package `siliconsmith`, Python ≥3.11; dependencies `mcp>=2.3,<3`, `pyyaml`, `pydantic>=2`; extras `[amaranth]` = `amaranth[builtin-yosys]>=0.5.10,<0.6`, `[myhdl]` = `myhdl>=0.11.52,<0.12`) and `uv.lock`.
- `src/siliconsmith/{__init__,_version,cli,_imagetag}.py`, plus the seams later issues build on: `result.py` (the result envelope), `server.py` with the tool registry in `tools/__init__.py`, `runners/base.py`, `tests/conftest.py`, and one real unit test.
- Keep `LICENSE` (Apache-2.0) and `THIRD_PARTY_LICENSES.md`. Add `LICENSES/Apache-2.0.txt`, `REUSE.toml`, `NOTICE`, and `CONTRIBUTING.md` with the GPL/LGPL rules (call GPL tools only as external processes; no vendoring) and the DCO rule (outside contributors sign off; agents never add `Signed-off-by`).
- SPDX headers on all source files.
- `.gitignore` additions: `tasks/`, `build/`, `sim_build/`, keeping `src/siliconsmith/templates/**` and `tests/fixtures/**`.
- Replace the CI placeholder with the `ci.yml` `lint` and `unit` jobs (PLAN.md section 14); Dependabot for GitHub Actions.
- Update `PLAN.md`, `ISSUES.md`, `CLAUDE.md` and `THIRD_PARTY_LICENSES.md` to the M0 design (container runner, pins, validation sequence, licensing), file the new issues and edit the changed ones on GitHub.
**Acceptance criteria**
- [x] `pip install -e .[amaranth,myhdl]` works on a clean Ubuntu 24.04 runner. *Proof:* `lint` job, pip-compat step.
- [x] CI is green, and `reuse lint` passes. *Proof:* all `ci.yml` jobs; the pinned `reuse lint` step.
- [x] The repo contains no third-party tool source or binaries. *Proof:* `lint` job, no-binaries step.

## [M0-2] Toolchain Docker image (simulation stage)
**Labels:** `track:digital` `milestone:M0` `docker`
**Tasks**
- `docker/Dockerfile` and a hash-locked `docker/requirements.lock` with the M0 pins in PLAN.md section 6: `ubuntu:24.04` by digest, apt `iverilog=12.0-2build2`, Verilator v5.046 copied from the official `verilator/verilator` image (by digest), `yowasp-yosys` 0.62 behind a `yosys` shim, `cocotb==2.1.0`, `pytest==8.4.2`, `amaranth[builtin-yosys]==0.5.10` with `AMARANTH_USE_YOSYS=builtin`, `myhdl==0.11.52`, `pywellen==0.25.7`. `linux/amd64` only.
- The image ships `/usr/share/doc/siliconsmith-toolchain/` with `SOURCES.txt`, `THIRD_PARTY_LICENSES.md`, a written offer for the corresponding source, and the license files of bundled components (wellen, Yosys, ABC, yosys-slang, slang).
- `runners/docker.py`: the container runner with the exact `docker run` arguments from PLAN.md section 5.1 (no network, read-only root, only `build/` writable, resource limits, rootless detection, `docker kill` on timeout). Worker cache plus `worker/{__main__,info}.py`.
- `toolchain_info` MCP tool.
- `image.yml` workflow and the `ci.yml` `integration` job (image tag `ctx-<h>`, pull or build, smoke test first, tests run as non-root with the exact runner arguments). The first push to `ghcr.io/rfingadam/siliconsmith-toolchain` comes from `image.yml`.
- Image size line in the README.
**Acceptance criteria**
- [x] `docker run --rm <image> sh -c 'iverilog -V && verilator --version && yosys -V'` succeeds in CI. *Proof:* `integration` smoke step and the `image.yml` smoke step.
- [x] `toolchain_info` returns JSON with every tool's version and the image id and digest. *Proof:* `test_toolchain_info_keys` (all values non-null, except `repo_digest` for a local build).
- [x] Image size is recorded in the README. *Proof:* the README line names the current `ctx-<h>` tag; `test_readme_image_tag_current`.

## [M0-3] MCP server core + project model
**Labels:** `track:digital` `milestone:M0` `mcp`
**Tasks**
- MCP server on the official MCP Python SDK v2 (`from mcp.server import MCPServer`), stdio only (streamable HTTP is M5-3). Project root from `--root`, `SILICONSMITH_ROOT`, `CLAUDE_PROJECT_DIR`, then the current directory. Server `instructions` text.
- `paths.py`: symlink-safe host file I/O (PLAN.md section 5.1) and the per-front-end write allow-list (PLAN.md section 5.3).
- `project.py` and `scaffold.py`: `siliconsmith.toml` holds `name` and `frontend` only. `create_project` lays out a TT-shaped project from a pinned `ttsky-verilog-template` snapshot in `templates/tt/` (with an `UPSTREAM` manifest of our patches) and generates the `tt_um_<name>` wrapper.
- Tools: `create_project`, `list_files`, `read_file`, `write_rtl` (full content, no patches), `write_test`, all returning the standard result `{ok, summary, details, artifacts, next_hints}`; usage errors raise `ToolError`.
- README config snippets: Claude Code (`.mcp.json` and `claude mcp add`), Cursor (`${workspaceFolder}`), Codex (`tool_timeout_sec`).
**Acceptance criteria**
- [x] `create_project(frontend=amaranth|myhdl|verilog)` creates a project with a TT-compatible `tt_um_<name>` wrapper (ports: `ui_in[7:0]`, `uo_out[7:0]`, `uio_in/uio_out/uio_oe[7:0]`, `ena`, `clk`, `rst_n`) and a cocotb test stub. *Proof:* `test_scaffold[fe]` (ports compared with the TT template snapshot) and an integration test that checks the ports with Yosys.
- [x] Path traversal attempts are rejected. *Proof:* `test_paths_*` (`..`, absolute paths, symlinks, NUL bytes, writes outside the allow-list).
- [x] README has working config snippets for Claude Code and Cursor. *Proof:* `test_readme_snippets` (launches the exact command over stdio and lists the tools; checks the Cursor JSON against its schema).

## [M0-4] Front-end interface: Amaranth, MyHDL, Verilog passthrough
**Labels:** `track:digital` `milestone:M0` `frontend`
**Tasks**
- One module per front-end under `frontends/`, plus `worker/emit.py`, which runs inside the container. Amaranth: module-level `top()` and `verilog.convert(top(), name=<name>, emit_src=False)` with Amaranth's bundled Yosys. MyHDL: `@block def <name>(...)` and `convert(hdl='Verilog', testbench=False, no_myhdl_header=True, timescale='1ns/1ps')`; `ToVerilogWarning` is an error. Verilog: passthrough. (PLAN.md section 5.2.)
- Core/wrapper contract: read the core's ports with Yosys `write_json`, check names, directions and widths against the TT pin names, and regenerate the `tt_um_<name>` wrapper.
- Lint with `verilator --lint-only -Wall --timing` (latches and multiple drivers are errors) and parse the findings. Map Python errors to a file and line under `hdl/`.
- `emit_verilog` MCP tool. Emitted output is byte-identical across runs.
**Acceptance criteria**
- [x] The same 8-bit counter emits valid Verilog from all three front-ends with an identical top-level port list. *Proof:* `test_emit_counter[fe]` compared with `tests/golden/counter_ports.json`; the output compiles with `iverilog -g2012` and lints clean.
- [x] A Python syntax or elaboration error in Amaranth/MyHDL source comes back with file and line in ≤20 lines. *Proof:* unit test (host-side `ast.parse`) and `test_emit_errors[amaranth,myhdl]` (the reported path is `hdl/<name>.py`).
- [x] A Verilator lint warning (e.g. width mismatch) is returned in a structured form. *Proof:* `test_lint_width` (`WIDTHTRUNC`/`WIDTHEXPAND` as `{code, file, line, col, msg}`) and parser fixtures.

## [M0-5] `run_tests` with cocotb on Icarus and Verilator
**Labels:** `track:digital` `milestone:M0` `sim`
**Tasks**
- `run_tests(simulator, test_filter, waves)`: one container call that re-emits, lints and runs the cocotb 2.1 Python runner (`tb` as top level, 1 ns / 1 ps timescale, Verilator with `--timing`), then parses the results file. Results go into the ledger (M0-8). Gate-level simulation is M1.
- `inspect_waves(path, signals, time_range, max_rows=50)` reads VCD or FST with pywellen and returns a compact value table.
- The 6-cell integration matrix (3 front-ends × 2 simulators), a seeded wrap bug, and a parity test against TT's own `test/Makefile` flow.
**Acceptance criteria**
- [x] Counter tests pass on **both** Icarus and Verilator for **all three** front-ends (CI matrix). *Proof:* `test_counter_sim[fe-sim]`, 6 cells.
- [x] A seeded bug (off-by-one wrap) produces a failure summary naming the test, the assertion, and the expected vs actual value, with the first failing assertion in ≤20 lines. *Proof:* `test_seeded_wrap_bug` asserts `{test, file:line, expected, actual}` and the 20-line limit.
- [x] Waveform output stays under 50 rows unless more are requested. *Proof:* `test_inspect_waves_rows`.

## [M0-6] Skill pack v1 + end-to-end agent demo
**Labels:** `track:digital` `milestone:M0` `skills` `docs`
**Tasks**
- Skills as package data in `src/siliconsmith/skills/<name>/SKILL.md`: `chip-design-loop`, `tt-rules`, `amaranth-style`, `myhdl-style`, `cocotb2-tests`. They reach the agent through the server's `instructions` and the `explain(topic)` tool.
- `docs/beginner-guide.md` + glossary (RTL, testbench, synthesis, netlist, GDS, DRC, LVS, slack).
- `docs/demos/`: a runner for headless `claude -p` sessions, `verify_demo.py`, sanitized Markdown transcripts, and a metrics table.
- The demo runner sets `CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS=0`. Claude Code moves an MCP call that runs longer than 2 minutes to the background, and in `claude -p` the result of such a call is lost.
- Update the status lines in the README and PLAN.md once M0 works.
**Acceptance criteria**
- [x] A recorded session (transcript in `docs/demos/`): starting from an empty dir and the prompt "8-bit counter on uo_out, count enable on ui_in[0]", the agent reaches passing tests in each front-end without any human code edits. *Proof:* `docs/demos/verify_demo.py` and the committed transcripts: the agent's tests **and** the held-out reference test pass on Icarus **and** Verilator for each front-end.
- [x] Number of loop iterations recorded per front-end. *Proof:* the metrics table, which lists every attempt.

## [M0-7] Spec capture (`spec.yaml`)
**Labels:** `track:digital` `milestone:M0` `mcp`
**Goal:** A short, reviewable spec that the owner can judge, and that is the single source of the clock, pins and requirements.
**Tasks**
- `spec.py`: the `spec.yaml` version 1 model (`title`, `summary`, `clock_hz`, `reset`, `pins{ui,uo,uio: {bit: meaning}}`, `requirements[{id: R<n>, text, limit?{min,typ,max,unit}}]`, `open_questions[]`; PLAN.md section 5.7).
- `write_spec` MCP tool; `create_project` writes a valid stub.
- `Covers:` parser: reads requirement ids from test docstrings with `ast`, without running the test file.
**Acceptance criteria**
- [x] `write_spec` rejects a missing `clock_hz`, bad requirement ids, an out-of-range pin index, and unknown keys, each with the field path, in ≤20 lines. *Proof:* one unit test per case.
- [x] `spec.yaml` is the only source of `clock_hz`, pins and requirements, and `create_project` writes a valid stub. *Proof:* unit tests.
- [x] `Covers:` tags are parsed without executing the test file; an unknown R-id gives a warning; a skipped test does not count as coverage; `limit` round-trips. *Proof:* unit tests (the fixture test file raises an error if it is ever imported).

## [M0-8] Validation ledger + status
**Labels:** `track:digital` `milestone:M0` `mcp`
**Goal:** Show the owner, in plain English, which checks pass for the current files, and keep that record across agent sessions.
**Tasks**
- `ledger.py`: `.siliconsmith/ledger.json` stores facts only (spec approval; one record per `emit_verilog` and `run_tests` run). Only the host writes it.
- Derived status per step (`pass`, `fail`, `stale`, `not_run`, `not_available`) with per-step input digests (PLAN.md section 5.7).
- `status` MCP tool and the `siliconsmith status <dir>` command, both writing the same `docs/verification.md`.
- `siliconsmith approve <dir>`: command line only, terminal confirmation, records git `user.name` and the spec hash. Documented as an honest-use convention; the `chip-design-loop` skill forbids agents from running it.
- `tests/reference/`: the frozen, held-out counter reference test.
**Acceptance criteria**
- [x] Each step goes stale only when its own inputs change. *Proof:* one unit test per input class (editing X makes exactly the dependent steps stale; for example, editing a nested test helper such as `test/sub/x.py` makes simulation and coverage stale).
- [x] Simulation and coverage steps are set only by unfiltered runs with at least one test, and coverage needs passing runs on both simulators at the current digest. *Proof:* unit and integration tests.
- [x] No MCP tool can approve the spec, and generated docs are not writable through the tools. *Proof:* tools/list and allow-list tests; `approve` refuses to run without a terminal.
- [x] The `status` tool and the command line give the same output; a corrupt or missing ledger gives `not_run` without a crash; M1–M3 steps show `not_available`. *Proof:* unit tests.
- [x] A frozen reference test for the counter is committed under `tests/reference/`. It assumes only the prompt: +1 per clock while `ui_in[0]` is 1, hold while it is 0, wrap from 255 to 0, and no particular reset value. *Proof:* committed with this issue's PR and run by CI and the M0-6 demo.

---

## [M1-0] Pinned sky130 files in the image, `tt.lock`, native Yosys decision, `run_tests(gl)` plumbing
**Labels:** `track:digital` `milestone:M1` `pdk` `infra`
**Goal:** Everything M1 depends on, pinned to the versions Tiny Tapeout uses, before any synthesis code is written. (Rescoped in the M1 plan pass on 2026-11-12: the LibreLane image pin, the ciel PDK volume, the CI image cache and the EQY option moved to [M2-1], where layout needs them.)
**Key constraints**
- The sky130 files that synthesis and gate-level simulation read are baked into the toolchain image (`/opt/pdk/sky130A`, read-only, about 17 MB) from the `fossi-foundation/ciel-releases` tag `sky130-8afc8346…` (open_pdks `8afc8346a57fe1ab7934ba5a6056ea8b43078e71`), each tarball checked against a pinned sha256 at build time. `/opt/pdk/manifest.json` lists the files and their sha256; `toolchain_info` reports it.
- `tt.lock` (repo root) records the shuttle tag, the tt-gds-action tag, the tt-support-tools commit, the LibreLane version and digests, and the sky130 hash per TT step (harden/GL `8afc8346…`, precheck `0536d02d…`). The Dockerfile's sky130 hash equals the `tt.lock` harden hash.
- Native Yosys decision: keep the M0 `yowasp-yosys` 0.62 (same version as LibreLane 3.0.14) for M1, recorded with measured times in PLAN.md section 6.
- `run_tests` gets the `gl` option; the status table gets its three M1 rows (Synthesis, Gate-level simulation, Equivalence), still `not_available`.
**Acceptance criteria**
- [ ] The image holds the pinned sky130 files, and the build verifies the tarball sha256. *Proof:* Dockerfile `sha256sum -c`; integration test `test_pdk_files_present`.
- [ ] `toolchain_info.pdk` is `{name: sky130A, open_pdks: 8afc8346…, libraries: [sky130_fd_sc_hd], corner: tt_025C_1v80}`. *Proof:* integration test `test_toolchain_info_pdk`.
- [ ] `tt.lock` records the shuttle tag, the tt-support-tools commit, the LibreLane version and digest, and the sky130 hash per step, and the Dockerfile ARG equals `tt.lock`. *Proof:* unit test `test_tt_lock_matches_dockerfile`.
- [ ] The native-Yosys decision is recorded with reasons. *Proof:* PLAN.md section 6 text in the PR.
- [ ] `run_tests(gl=true)` with `simulator='verilator'` raises a ToolError saying gate-level simulation runs on Icarus only. *Proof:* unit test.

## [M1-1] `synthesize` tool on sky130
**Labels:** `track:digital` `milestone:M1` `synth`
**Tasks**
- Yosys synthesis to sky130_fd_sc_hd with the liberty file and cell lists baked into the image by M1-0. Follow LibreLane 3.0.14's synthesis script (`librelane/scripts/pyosys/synthesize.py`), with the `no_synth.cells` and `drc_exclude.cells` lists as `-dont_use`. Sky130 only, flat top; the tool has no `target` or `flatten` options.
- `spec.yaml` gains an optional `tiles` field (default `"1x1"`; Tiny Tapeout values only). `src/config.json` `CLOCK_PERIOD` = 1e9 / `clock_hz` (ns) and `info.yaml` `clock_hz` and `tiles` are written by the tools from the spec (at `create_project` and `write_spec`, and re-synced before every M1 tool's digest snapshot, which also fixes M0 projects). Warn above 66 MHz (the TT IO limit).
- Parse `stat -json`; detect latches, combinational loops, multiple drivers and undriven outputs with source locations; MyHDL latches are named by the generated `src/<name>.v` line and the `hdl/` function and line.
**Acceptance criteria**
- [ ] Counter synthesis reports cell count, flip-flops vs combinational cells, area (µm²), top cell types and utilization, on all three front-ends. *Proof:* integration `test_synth_counter[fe]`.
- [ ] A seeded latch (incomplete `if` in combinational logic) in Verilog and in MyHDL is reported with file and line through `synthesize` (its emit and lint stage stops it, as in M0). Amaranth is not applicable (its combinational domain cannot infer latches). An explicit `always_latch` that passes lint is mapped with `latch_map.v` and reported as a warning. MyHDL names the generated `src/<name>.v:line` and the `hdl/` function and line. *Proof:* integration `test_synth_latch[verilog,myhdl]`.
- [ ] The utilization estimate is cell area ÷ (core area × `PL_TARGET_DENSITY_PCT`/100), where core area is the tile die area (`tile_sizes.yaml`) minus TT's `config.json` margins. It warns above 0.9, predicts failure above 1.0, says it is an estimate (layout in M2 gives the real number), and a bad `tiles` value names the allowed ones. *Proof:* unit test with a known cell area and the 1x1 core area.
- [ ] `src/config.json` `CLOCK_PERIOD` and `info.yaml` `clock_hz`/`tiles` come from `spec.yaml`, keeping TT's `"//"` comment keys; more than 66 MHz warns. *Proof:* unit tests.
- [ ] The script follows LibreLane 3.0.14's synthesis order with its do-not-use lists as `-dont_use`; the counter netlist has no do-not-use cell and no `lpflow_` cell. *Proof:* integration assertion on the netlist.
- [ ] Combinational loops, multiple drivers and undriven outputs are reported with source locations. *Proof:* unit tests on Yosys `check` output fixtures.
- [ ] A design far over budget stops before `abc` with a plain "too big for <tiles>" message, and synthesis has its own timeout. *Proof:* unit test and an integration test with an oversize fixture.

## [M1-2] Gate-level simulation
**Labels:** `track:digital` `milestone:M1` `sim`
**Tasks**
- `run_tests(gl=true)` on Icarus (as TT does), against the Yosys netlist from M1-1 plus the PDK's `sky130_fd_sc_hd` `primitives.v` and `sky130_fd_sc_hd.v`, with the defines `FUNCTIONAL`, `SIM` and `UNIT_DELAY=#1`. Do **not** define `USE_POWER_PINS` or `GL_TEST`: those are for LibreLane's powered netlist (M2/M3), and with them the unpowered Yosys netlist simulates as all X or fails to elaborate.
- Verilator gate-level simulation is not in scope.
**Acceptance criteria**
- [ ] `run_tests(simulator='icarus', gl=true)` reruns the cocotb tests on the sky130 netlist with `primitives.v` and `sky130_fd_sc_hd.v`, defines `FUNCTIONAL SIM UNIT_DELAY=#1` (never `USE_POWER_PINS` or `GL_TEST`), and passes for the counter on all three front-ends. *Proof:* integration `test_gl_counter[fe]`.
- [ ] A seeded `initial q = 0;` instead of a reset passes RTL and fails GL; the report names the signal and the first differing cycle with RTL vs GL values (sampled just before each rising `clk`, because the cell models delay flop outputs by 1 ns). *Proof:* integration `test_gl_mismatch`.
- [ ] GL runs never change the RTL simulation or coverage rows and never delete the agent's RTL waves (separate `icarus-gl` build, sim and wave folders). *Proof:* unit (ledger) and integration tests.

## [M1-3] RTL-vs-netlist equivalence check
**Labels:** `track:digital` `milestone:M1` `synth`
**Goal:** Prove the synthesized netlist does the same thing as the RTL, independent of how good the tests are.
**Key constraints**
- `check_equivalence(project)` uses Yosys's built-in `equiv_*` passes (same pinned binary, ISC) against cell models read from the liberty file. EQY in the LibreLane image becomes an option in [M2-1]. Do not rely on TT's separate `eqy_test` action; it pins a third sky130 hash and is out of date.
- If the spike cannot prove the counter, a hidden-state shift register and an FSM on all front-ends, this issue moves to [M2-1] and M1 closes without it, said plainly in PLAN.md and here.
- The skills say plainly that equivalence does not catch reset or initial-value bugs (gate-level simulation does).
**Acceptance criteria**
- [ ] `check_equivalence` (which re-synthesizes in the same container call; `build/` is never evidence) proves RTL and netlist equivalent for the counter, a hidden-state shift register (parity output) and an FSM, on the front-ends that can express them. *Proof:* integration `test_equiv[fixture-fe]`.
- [ ] A seeded non-equivalent netlist is reported as not equivalent, naming the signal. *Proof:* integration `test_equiv_mismatch` (a test-only script runs the worker's equivalence function in the container on a hand-edited fixture netlist; the production worker has no tamper hook).
- [ ] "Could not prove" is reported as not proven, `ok=false`, distinct from not equivalent, never a pass. *Proof:* unit test.
- [ ] `status` shows Synthesis, Gate-level simulation and Equivalence as pass for the counter. Inputs: synthesis = check inputs + `src/config.json` + `info.yaml` + image; gate-level = simulation inputs + synthesis inputs; equivalence = synthesis inputs. Each goes stale only on its own inputs; layout, TT checks and silicon stay `not_available`. *Proof:* unit test per input class (the stale matrix in `tests/test_status.py` extended, with a `src/config.json` row) and an integration test.

---

## [M2-1] LibreLane integration (OpenLane 2 successor)
**Labels:** `track:digital` `milestone:M2` `layout`
**Tasks**
- Run LibreLane from `ghcr.io/librelane/librelane:3.0.14` referenced by the digest in `tt.lock` (the version tt-gds-action installs at `ttsky26d`/`ttsky26e`), started directly with our container flags (PLAN.md section 5.1). Never `librelane --dockerized` (it mounts the whole `$HOME` read-write). Never a Python dependency of our package (it pulls in the GPL `klayout` module).
- The full sky130 PDK lives in a Docker volume filled by `ciel` at open_pdks `8afc8346a57fe1ab7934ba5a6056ea8b43078e71`; fetch only the libraries we need (`sky130_fd_sc_hd`) where that is enough. Cache the LibreLane image and the PDK volume in CI. (Moved here from M1-0 in the M1 plan pass: M1 bakes only ten sky130 files into the toolchain image.)
- Consider EQY from the LibreLane image as an option next to the Yosys equivalence passes that M1-3 uses; record its license in `THIRD_PARTY_LICENSES.md` if it is added.
- Versions come from `tt.lock`.
- `run_layout(stage=full|floorplan|place|cts|route|signoff, config_overrides)` with a TT 1x1 die-area config.
**Acceptance criteria**
- [ ] Counter → GDS on sky130A at 1x1 tile size, 0 DRC (Magic + KLayout), LVS clean, and setup and hold slack ≥ 0 at 50 MHz in every STA corner LibreLane reports for the TT config.
- [ ] Runtime and peak RAM measured on the GitHub `ubuntu-24.04` runner (laptop numbers are informational, labelled by CPU architecture).
- [ ] Failing step is named in `summary` when the flow fails.

## [M2-2] `read_report` parsers (timing, DRC, LVS, antenna, area, power)
**Labels:** `track:digital` `milestone:M2` `parsers`
**Acceptance criteria**
- [ ] Parsers are unit-tested against real saved LibreLane run fixtures.
- [ ] `read_report(kind=timing, top_n=5)` returns the worst paths with startpoint, endpoint, slack and corner.
- [ ] A seeded oversize design in a 1x1 tile returns a utilization/placement failure plus `next_hints` (raise tiles, reduce logic). The fixture: two 64-bit shift registers loaded serially from `ui_in[7:0]`, a registered 64×64 product, XOR-reduced to `uo_out`, so synthesis cannot optimize the logic away.

## [M2-3] `render_layout`
**Labels:** `track:digital` `milestone:M2`
**Acceptance criteria**
- [ ] PNG of the final GDS is produced via the KLayout CLI (external process; GPL tool not imported).
- [ ] Image path is returned and linked from the run summary.

## [M2-4] Debug skills (debug-timing, debug-drc)
**Labels:** `track:digital` `milestone:M2` `skills` `docs`
**Goal:** Teach the agent, and the owner, how to read the parsed layout reports and what to try next.
**Key constraints**
- `SKILL.md` format under `src/siliconsmith/skills/`, served through the server `instructions` and `explain`, like the M0 skills.
- Use LibreLane 3 names (`PL_TARGET_DENSITY_PCT`, not the old `PL_TARGET_DENSITY`) and TT's tile sizes from `tile_sizes.yaml`.
- Usual fixes: pipeline a stage, lower the clock, raise tiles, adjust density. Plain English for a beginner.
Acceptance criteria are finalized in the milestone's plan-mode pass.

## [M2-5] Long-running job handles + progress notifications
**Labels:** `track:digital` `milestone:M2` `mcp`
**Goal:** Layout runs take minutes; they must not hit client timeouts or block the agent loop.
**Key constraints**
- Plain tool-level job handles (start, status, wait, cancel), not MCP Tasks. A detached container per run; job state kept under `build/` so status survives a server restart.
- MCP progress notifications while waiting. Client limits to design for: Codex has a per-server tool timeout (`tool_timeout_sec`); Claude Code aborts a stdio call after 30 minutes without progress and moves calls longer than 2 minutes to the background.
Acceptance criteria are finalized in the milestone's plan-mode pass.

---

## [M3-1] `export_tt`: Tiny Tapeout submission repo
**Labels:** `track:digital` `milestone:M3` `tinytapeout`
**Tasks**
- Write the export to a fixed directory inside the project (for example `export/tt/`), never to an agent-chosen `repo_dir`.
- Generate the repo in the current `ttsky-verilog-template` format: `info.yaml` generated from `spec.yaml` and the project with `yaml.safe_dump` (title, author, description, `language` = Amaranth/MyHDL/Verilog as free text, `clock_hz`, `tiles`, `top_module`, `source_files`, full pinout), `src/`, `test/` (cocotb), `docs/info.md`, and `.github/workflows/*.yaml` using `TinyTapeout/tt-gds-action@<shuttle tag>` from `tt.lock`. Template files (`test/Makefile`, workflows) are copied byte-for-byte from the pinned template.
- For Amaranth/MyHDL: TT's tools read only Verilog, so the generated Verilog is committed in `src/` and listed in `source_files`; the Python source goes in `hdl/`. The docs say the Python source is what you edit and the Verilog is what TT builds.
**Acceptance criteria**
- [ ] Exported counter repo, pushed **by the owner** to a test repo, passes TT's `gds`, `precheck`, and `gl_test` Actions.
- [ ] `docs/info.md` explains how to test on the TT demo board in plain English.
- [ ] Nothing is ordered or submitted automatically.

## [M3-2] `tt_precheck` locally
**Labels:** `track:digital` `milestone:M3` `tinytapeout`
**Tasks**
- Run TT's precheck in TT's own environment: the `nix-shell` defined by tt-support-tools `precheck/default.nix` (Magic and KLayout versions differ from the ones in the LibreLane image), with the sky130 PDK at the precheck hash `0536d02d875c8f67dd7cca3902ac457e62f20005` from `tt.lock`, and the export directory mounted read-only.
**Acceptance criteria**
- [ ] Runs TT's precheck (from `tt-support-tools` / `tt-gds-action`) locally or documents why only CI can run it.
- [ ] Failures are mapped to structured hints (e.g. missing pinout entry, wrong top module name).

## [M3-3] Docs: "Your first chip" walkthrough + cost note
**Labels:** `track:digital` `milestone:M3` `docs`
**Acceptance criteria**
- [ ] Step-by-step guide from idea to submission-ready repo.
- [ ] Cost section links the official [TT calculator](https://app.tinytapeout.com/calculator) and says prices change. As of 2026-11-11 the calculator default view showed €70/tile, €300/devkit, €15 shipping (1 tile + devkit + shipping = €385). Re-check before any order.

## [M3-4] Automated tt-compat gate + spec→info.yaml/docs generation
**Labels:** `track:digital` `milestone:M3` `tinytapeout` `infra`
**Goal:** Prove in our own CI, on every relevant change, that an exported repo passes TT's checks, so the owner's manual push is only a one-time confirmation.
**Key constraints**
- `tt-compat.yml` runs `export_tt` for the counter, then TT's `tt-gds-action` (`gds`, `precheck`, `gl_test`) in one workflow run, at the shuttle tag and tt-support-tools commit pinned in `tt.lock` (TT moves its tags, and the action uses tt-support-tools `main` by default).
- `info.yaml` (pinout, `clock_hz`, `top_module`, `source_files`) and the pin table and test steps in `docs/info.md` are generated from `spec.yaml`, so there is one source of truth.
Acceptance criteria are finalized in the milestone's plan-mode pass.

## [M3-5] Demo-board bring-up test (microcotb) + shuttle guide
**Labels:** `track:digital` `milestone:M3` `tinytapeout` `docs`
**Goal:** Turn the pin-level cocotb tests into a test the owner can run on the real chip on the TT demo board, and explain how to pick and use a shuttle.
**Key constraints**
- `export_tt` generates a bring-up script for the demo board from the pin-level requirement tests, using microcotb (cocotb-style tests on the demo board). It is marked "unverified until run on hardware", and silicon bring-up stays an owner step in the validation sequence.
- Requirement tests use `ClockCycles`, not absolute `Timer` delays, so they also work at the board's slower clocks.
- The shuttle guide is plain English and never orders or submits anything; prices and dates point at TT's own pages.
- microcotb is LGPL-2.1 according to its repository's `LICENSE` (checked 2026-11-11); verify it again from the official repo when it is added and record it in `THIRD_PARTY_LICENSES.md`. Exported repos import it unmodified; our package does not.
Acceptance criteria are finalized in the milestone's plan-mode pass.

## [M3-6] Distribution: Claude Code plugin, install-skills CLI, MCP prompts/resources
**Labels:** `track:digital` `milestone:M3` `skills` `mcp`
**Goal:** Make the skills and the server easy to install in each agent, beyond the M0 delivery through server `instructions` and `explain`.
**Key constraints**
- A Claude Code plugin that bundles the MCP server config and the `SKILL.md` skills (check the current plugin manifest format when building it).
- `siliconsmith install-skills --client claude|codex|cursor [--user]` copies the skills into the client's skills folder.
- MCP prompts and resources expose the same skill text.
- One source of skill text (package data); no copies that can drift.
Acceptance criteria are finalized in the milestone's plan-mode pass.

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

## [M4-6] Mutation / weak-test check
**Labels:** `track:digital` `milestone:M4` `eval`
**Goal:** Catch tests that pass but do not really test the design.
**Key constraints**
- Inject faults into the design (for example with Yosys `mutate`) and report the share of mutants the cocotb tests catch.
- Runs in the toolchain container like every other tool; report in the standard result shape.
Acceptance criteria are finalized in the milestone's plan-mode pass.

---

## [M5-1] (optional) Extra PDKs: GF180MCU and IHP SG13G2
**Labels:** `track:digital` `milestone:M5` `pdk`
**Acceptance criteria**
- [ ] Counter hardens on at least one of `gf180mcuD` / `ihp-sg13g2` using TT's `ttgf-*` / `ttihp-*` template conventions.
- [ ] Confirm how current GF180 builds are fetched (Google's `gf180mcu-pdk` repo is archived; open_pdks/ciel is the expected path).

## [M5-2] Digital skills point at the analog/RF track
**Labels:** `track:digital` `milestone:M5` `docs` `skills`
**Acceptance criteria**
- [ ] `src/siliconsmith/skills/analog-reality/SKILL.md` (and the beginner docs) state the digital/analog split, point at the analog/RF issues, and do not treat analog as "docs only."
- [ ] The skill stays honest: no 2.4 GHz Bluetooth radio claim, no SAW/BAW fab claim.

## [M5-3] Infra follow-ups: streamable HTTP transport, arm64 image, hosted GPL source bundle
**Labels:** `track:digital` `milestone:M5` `infra` `docker`
**Goal:** Infrastructure that M0 deliberately left out.
**Key constraints**
- Streamable HTTP transport: whoever can call the tools can start containers, so bind to loopback only unless a bearer token is configured; validate `Origin`; turn DNS-rebinding protection on explicitly (the MCP SDK enables it automatically only for localhost hosts); a test shows a request with a foreign `Origin` is refused.
- arm64 image (`linux/arm64`) for Apple Silicon and ARM Linux, with the same pins where upstream provides them.
- A hosted corresponding-source bundle per published image (for example a release asset or OCI artifact named after the image digest), in addition to `SOURCES.txt` and the written offer. It is the backstop if the Ubuntu archive or an upstream source that `SOURCES.txt` points at disappears: for the GPL-3.0 components we remain responsible for that source staying available (`THIRD_PARTY_LICENSES.md` rule 3).
Acceptance criteria are finalized in the milestone's plan-mode pass.

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
- [ ] Exports a chosen analog block (prefer the oscillator if it is clean) in the current `ttsky-analog-template` format: `info.yaml` (tiles 1x2 or 2x2, `analog_pins`, pinout, `uses_vapwr` (formerly `uses_3v3`) if needed), GDS/LEF paths, `src/project.v` stub, `docs/info.md`.
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
- [ ] `docs/` + `src/siliconsmith/skills/acoustic-reality/SKILL.md` explain what SAW/BAW devices are, why they are not CMOS Tiny Tapeout parts, and that this track will not fabricate them.
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
