# Siliconsmith

An open-source AI chip-design agent. One MCP server, one pinned Docker toolchain, and a write-test-fix loop that an AI coding agent (Claude Code, Cursor, Codex) can run end to end.

**Status:** digital milestone M0 is in progress. The project tools (create, list, read and write files) work today; emit and simulation arrive in later M0 steps; [ISSUES.md](ISSUES.md) and the GitHub issues track what has landed.

Three tracks share the same server and toolchain:

1. **Digital** (start here). Amaranth, MyHDL, or Verilog in; cocotb tests on Icarus and Verilator; Yosys synthesis; LibreLane layout on open PDKs; a Tiny Tapeout submission repo out. Milestone M0 is first.
2. **Analog/RF**, aimed at the two halves of a Bluetooth-style chip (digital baseband plus RF/analog front end). Xschem, ngspice, Magic/KLayout, netgen, and openEMS. First design targets: an oscillator or VCO, then an LNA. A working 2.4 GHz Bluetooth radio is not a goal.
3. **SAW/BAW acoustic filters**, research and stretch. Design and simulation only. There is no open fab path for these devices. Models (Mason, BVD) plus openEMS and Elmer FEM.

License: [Apache-2.0](LICENSE). Open-source tools are called as external processes, never vendored. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

If you are implementing this, read [CLAUDE.md](CLAUDE.md) and [PLAN.md](PLAN.md), then work milestone by milestone, digital M0 first. Issues are listed in [ISSUES.md](ISSUES.md) and on the GitHub issue tracker.

## Use it with an AI coding agent

Siliconsmith is an MCP server. An MCP server is a small program that your AI coding agent starts
and talks to, so the agent gets extra tools (here: tools that create and edit chip projects).
The server runs on your machine and talks to the agent over standard input and output, so
nothing is exposed on the network.

Today the server offers `toolchain_info`, `create_project`, `list_files`, `read_file`,
`write_spec`, `write_rtl`, `write_test`, `emit_verilog`, `run_tests`, `inspect_waves`,
`status` and `explain`. `explain` answers from the packaged skills and a plain-English glossary: ask it for
`explain('chip-design-loop')` first. New to digital design? Read the
[beginner guide](docs/beginner-guide.md). Start
with `write_spec`: `spec.yaml` says the clock, the pins and the numbered requirements, and
everything else follows from it. `emit_verilog` turns the design (Amaranth, MyHDL or Verilog)
into a checked Verilog core plus the Tiny Tapeout wrapper and lints it with Verilator.
`run_tests` runs your cocotb tests against the design on Icarus Verilog or Verilator (it emits
and lints first), and a failure comes back as the test name, the file and line, the assertion,
and the expected and actual values. With `waves=True` it keeps a wave file, and `inspect_waves`
shows how chosen signals changed as a short table. Emitting and testing need Docker and the
toolchain image.

`status` shows, in plain English, how far the design has got: is the spec valid, has the owner
approved it, does the Verilog check out, do the tests pass on both simulators, and does every
requirement have a passing test. Each step is `pass`, `fail`, `stale` (a file it depends on has
changed since it ran, so run it again), `not run` or `not available` (it belongs to a later
milestone). The same table is written to `docs/verification.md` and printed by
`siliconsmith status <project folder>`. A test run with a `test_filter` never counts, because it
did not run every test.

Approving the spec is your step, not the agent's. Read `spec.yaml`, then run
`siliconsmith approve <project folder>` in a terminal; it shows the requirements, asks you to
type `approve`, and records your git `user.name` and the spec's hash in
`.siliconsmith/ledger.json`. There is no tool for this and the command refuses to run without a
terminal. That is an honest-use convention, not a security boundary: it keeps an honest agent
from approving its own work, and the ledger shows who approved what.

### Install

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/RFingAdam/siliconsmith.git
cd siliconsmith
uv tool install .
```

This puts the `siliconsmith-mcp` command on your `PATH` (uv usually uses `~/.local/bin`). With
plain pip, use `pip install -e .` inside a virtual environment instead, and either activate that
environment before you start the agent or write the full path to `siliconsmith-mcp` in the
`command` field below.

The server keeps all projects in one **root** folder, one subfolder per project. It picks the
root from `--root`, then the `SILICONSMITH_ROOT` environment variable, then
`CLAUDE_PROJECT_DIR`, then the folder it was started in. It refuses `/` and your home folder
unless you pass them with `--root` on purpose. `--default-frontend amaranth|myhdl|verilog` lets
`create_project` skip the `frontend` argument.

### Claude Code

Claude Code starts the server from your project folder and sets `CLAUDE_PROJECT_DIR` to it, so
the root is that folder. Put this in a `.mcp.json` file at the top of the folder (this is the
snippet that `tests/test_readme_snippets.py` launches over stdio):

<!-- snippet: claude-code-mcp-json -->
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

The same thing as a single command:

<!-- snippet: claude-code-add -->
```sh
claude mcp add --scope project siliconsmith -- siliconsmith-mcp
```

Claude Code asks you to approve a project-scoped server the first time. Run `/mcp` to check
that `siliconsmith` is connected.

### Cursor

Create `.cursor/mcp.json` in your project folder. Cursor replaces `${workspaceFolder}` with the
folder you opened (the test checks this file's structure against the Cursor `mcp.json` format):

<!-- snippet: cursor-mcp-json -->
```json
{
  "mcpServers": {
    "siliconsmith": {
      "command": "siliconsmith-mcp",
      "args": ["--root", "${workspaceFolder}"]
    }
  }
}
```

### Codex

Add this to `~/.codex/config.toml`, and change the path to the folder that should hold your
chip projects. `tool_timeout_sec` is how long Codex waits for one tool call; simulations and
layout runs can take minutes, so give it plenty:

<!-- snippet: codex-config-toml -->
```toml
[mcp_servers.siliconsmith]
command = "siliconsmith-mcp"
args = ["--root", "/path/to/your/chip-projects"]
startup_timeout_sec = 30
tool_timeout_sec = 1800
```

### Try it

Ask the agent: "Use Siliconsmith to create a verilog project called `adder` and show me its
files." It should call `create_project` and then `list_files`.

## Toolchain image

Every simulator and Python tool runs inside one Docker image, so the only things you install on your own machine are Docker and Python. The image holds Icarus Verilog, Verilator 5.046, Yosys 0.62, cocotb, Amaranth, MyHDL and a waveform reader, and it has no network access when Siliconsmith runs it.

    ghcr.io/rfingadam/siliconsmith-toolchain:ctx-ab445ef2bfa2

Size (linux/amd64): about 800 MB unpacked, and about 255 MB to download. The unpacked figure is the sum of the layer sizes that `docker history --human=false` lists, which gives the same number on every Docker setup. `docker images` can show a larger figure (1.05 GB on a Docker that uses the containerd image store) because it counts disk use, not the size of the files.

Get it with `docker pull ghcr.io/rfingadam/siliconsmith-toolchain:ctx-ab445ef2bfa2`. The tag changes whenever anything in `docker/` changes, and the server always asks for the tag that matches the checkout you are running. To use a different image, set `SILICONSMITH_IMAGE` (for the server, and pass the same name to `siliconsmith status --image`, because the image is part of what makes a result stale). The tool `toolchain_info` checks that Docker and the image work and lists every tool's version.

To build it yourself instead: `docker build -t ghcr.io/rfingadam/siliconsmith-toolchain:ctx-ab445ef2bfa2 docker/` (about two minutes with a warm cache).
