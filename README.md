# Siliconsmith

An open-source AI chip-design agent. One MCP server, one pinned Docker toolchain, and a write-test-fix loop that an AI coding agent (Claude Code, Cursor, Codex) can run end to end.

**Status: planning.** This repository is a starter kit: the plan, the issue list, and the rules for the first implementation. There is no tool code yet.

Three tracks share the same server and toolchain:

1. **Digital** (start here). Amaranth, MyHDL, or Verilog in; cocotb tests on Icarus and Verilator; Yosys synthesis; LibreLane layout on open PDKs; a Tiny Tapeout submission repo out. Milestone M0 is first.
2. **Analog/RF**, aimed at the two halves of a Bluetooth-style chip (digital baseband plus RF/analog front end). Xschem, ngspice, Magic/KLayout, netgen, and openEMS. First design targets: an oscillator or VCO, then an LNA. A working 2.4 GHz Bluetooth radio is not a goal.
3. **SAW/BAW acoustic filters**, research and stretch. Design and simulation only. There is no open fab path for these devices. Models (Mason, BVD) plus openEMS and Elmer FEM.

License: [Apache-2.0](LICENSE). Open-source tools are called as external processes, never vendored. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).

If you are implementing this, read [CLAUDE.md](CLAUDE.md) and [PLAN.md](PLAN.md), then work milestone by milestone, digital M0 first. Issues are listed in [ISSUES.md](ISSUES.md) and on the GitHub issue tracker.
