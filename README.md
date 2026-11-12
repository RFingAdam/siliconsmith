# Siliconsmith

An open-source AI chip-design agent. One MCP server, one pinned Docker toolchain, and a write-test-fix loop that an AI coding agent (Claude Code, Cursor, Codex) can run end to end.

**Status:** digital milestone M0 is in progress. The tools are not usable yet; [ISSUES.md](ISSUES.md) and the GitHub issues track what has landed.

Three tracks share the same server and toolchain:

1. **Digital** (start here). Amaranth, MyHDL, or Verilog in; cocotb tests on Icarus and Verilator; Yosys synthesis; LibreLane layout on open PDKs; a Tiny Tapeout submission repo out. Milestone M0 is first.
2. **Analog/RF**, aimed at the two halves of a Bluetooth-style chip (digital baseband plus RF/analog front end). Xschem, ngspice, Magic/KLayout, netgen, and openEMS. First design targets: an oscillator or VCO, then an LNA. A working 2.4 GHz Bluetooth radio is not a goal.
3. **SAW/BAW acoustic filters**, research and stretch. Design and simulation only. There is no open fab path for these devices. Models (Mason, BVD) plus openEMS and Elmer FEM.

License: [Apache-2.0](LICENSE). Open-source tools are called as external processes, never vendored. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

If you are implementing this, read [CLAUDE.md](CLAUDE.md) and [PLAN.md](PLAN.md), then work milestone by milestone, digital M0 first. Issues are listed in [ISSUES.md](ISSUES.md) and on the GitHub issue tracker.

## Toolchain image

Every simulator and Python tool runs inside one Docker image, so the only things you install on your own machine are Docker and Python. The image holds Icarus Verilog, Verilator 5.046, Yosys 0.62, cocotb, Amaranth, MyHDL and a waveform reader, and it has no network access when Siliconsmith runs it.

    ghcr.io/rfingadam/siliconsmith-toolchain:ctx-ab445ef2bfa2

Size (linux/amd64): about 800 MB unpacked, and about 255 MB to download. The unpacked figure is the sum of the layer sizes that `docker history --human=false` lists, which gives the same number on every Docker setup. `docker images` can show a larger figure (1.05 GB on a Docker that uses the containerd image store) because it counts disk use, not the size of the files.

Get it with `docker pull ghcr.io/rfingadam/siliconsmith-toolchain:ctx-ab445ef2bfa2`. The tag changes whenever anything in `docker/` changes, and the server always asks for the tag that matches the checkout you are running. To use a different image, set `SILICONSMITH_IMAGE`. The tool `toolchain_info` checks that Docker and the image work and lists every tool's version.

To build it yourself instead: `docker build -t ghcr.io/rfingadam/siliconsmith-toolchain:ctx-ab445ef2bfa2 docker/` (about two minutes with a warm cache).
