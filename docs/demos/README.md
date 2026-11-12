# Recorded demos

These are recordings of a headless Claude Code session designing the counter that
Siliconsmith's whole first milestone is built around. The prompt is exactly:

> 8-bit counter on uo_out, count enable on ui_in[0]

## What a demo proves

- **One prompt, an empty folder, no human edits.** The agent starts with nothing and gets that one
  sentence. Nobody touches a file during the session.
- **Only Siliconsmith's tools.** The agent has no shell, no file editor, no web access, no
  project notes (`CLAUDE.md`), no hooks and no extra plugins. It can only call the MCP tools this
  repository provides. So if it works, it worked because the tools and the built-in guidance
  (`explain`) were good enough.
- **The design does what the prompt asked, not just what the agent's own tests say.** An agent can
  write a test that agrees with its own mistake. So after the session we run a second, frozen test
  (`tests/reference/test_counter_reference.py`) that the agent never saw. It only assumes the
  prompt: the count goes up by one on each clock while `ui_in[0]` is 1, holds while it is 0, and
  wraps from 255 to 0. We wrote it before the demo, and it is not changed to fit a result.
- **Two different simulators agree.** Both the agent's tests and the reference test are run on
  Icarus and on Verilator, so four runs have to pass.
- **How many tries it took.** Each `run_tests` call is one turn of the write-test-fix loop; the
  table counts them.

## What a demo does not prove

- **Simulation is not silicon.** Nothing here has been synthesized, laid out or sent to a fab.
  Those steps arrive in milestones M1 to M3.
- It is one counter, one prompt and one model on one day. It says the loop works; it does not say
  the tools are good at every design.
- The agent's spec is still unapproved. Only the owner approves a spec, on the command line, and
  no demo does that.
- **The checks catch honest mistakes, not a hostile design.** The design's own code runs while it
  is turned into Verilog (that is how Amaranth and MyHDL work), so a design written to cheat could
  tamper with a test result. The checks above stop the obvious shortcuts (hand edits, swapped
  tests, start-up hooks); they are not a security boundary.

## How the isolation is done

`run_demo.py` starts `claude -p` with a clean environment (no API key, no shell setup), only the
Siliconsmith MCP server, `--tools ""` plus a deny list, `--permission-mode dontAsk`, no user or
local settings, and a demo folder under `/tmp` so no parent folder can hold an `AGENTS.md` or
`CLAUDE.md` (the runner checks every parent folder and records that in `meta.json`, together with
the fact that it set `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`). `verify_demo.py` then reads the raw
event stream and the debug log and checks that the agent really had only our tools, that the
only MCP server was ours, that every plugin was built in (and the memory plugin was loaded), that
no hook ran, that the debug log never names a memory file, that every tool call was ours, and
that the session ended with result `success`.

It also ties the project folder to the session: every file the agent could write (the design,
`spec.yaml`, the tests) must be byte-for-byte the last thing the session wrote to it, or still be
the untouched starter file, and no `sitecustomize.py`, `usercustomize.py` or `.pth` file may exist
(Python runs those by itself inside the simulator). The reference re-run happens on a copy whose
`test/` folder is rebuilt from scratch, so only the frozen reference test runs, and it must be
the only test that ran. Last, the checks look in the sanitized transcript for a home path, an
e-mail address, a session id, init fields or system-prompt text. If any are found, the transcript
is not copied into this folder; the attempt is still listed below. The raw stream is never
committed.

## How to re-run

```bash
# once: pull the toolchain image named in the "Toolchain image" section of the top-level README
uv run python docs/demos/run_demo.py --frontend verilog                 # or amaranth, myhdl
```

The runner prints the demo folder (under `/tmp`) and the verification result, and exits 0 only if
everything passed. Add `--record` to file the transcript and a row in the table below. To verify an
existing demo folder again: `uv run python docs/demos/verify_demo.py <folder> [--record]`.

## Every attempt

Every attempt is listed, passing or not. If an attempt fails because of a problem in a tool or a
skill, the fix is a commit to this repository and the next attempt is a new row. A transcript is
never edited.

<!-- metrics:start -->
| Date | Front-end | Model | Claude Code | Siliconsmith | Turns | Result | run_tests calls | Cost (USD) | Minutes | Agent: Icarus | Agent: Verilator | Reference: Icarus | Reference: Verilator | Status | Passed | Transcript |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-12 | verilog | claude-sonnet-5-5 | 2.1.322 | ec7ed2d | 14 | success (exit 0) | 4 | 0.14 | 1.0 | pass | pass | pass | pass | spec valid, check, sim x2, coverage pass; approval open | yes | [verilog-20261112-1.md](verilog-20261112-1.md) |
| 2026-11-12 | myhdl | claude-sonnet-5-5 | 2.1.322 | ec7ed2d+dirty | 12 | success (exit 0) | 2 | 0.12 | 0.8 | pass | pass | pass | pass | spec valid, check, sim x2, coverage pass; approval open | yes | [myhdl-20261112-1.md](myhdl-20261112-1.md) |
| 2026-11-12 | amaranth | claude-sonnet-5-5 | 2.1.322 | ec7ed2d+dirty | 12 | success (exit 0) | 2 | 0.12 | 0.8 | pass | pass | pass | pass | spec valid, check, sim x2, coverage pass; approval open | yes | [amaranth-20261112-1.md](amaranth-20261112-1.md) |
<!-- metrics:end -->
