# CLAUDE.md — rules for work in this repository

You are implementing Siliconsmith. Read this file and `PLAN.md` before you write a plan or any code. Then use plan mode **once per milestone**, get the plan approved, and only then build that milestone.

## What this project is

Siliconsmith is an Apache-2.0 MCP server plus a skill pack and a pinned Docker toolchain. It lets an AI coding agent design small chips with open-source tools only. There are three tracks (digital, analog/RF, acoustic filters) that share one MCP server, one toolchain image, and one agent loop. Details, architecture, and acceptance criteria are in `PLAN.md`. Issues are in `ISSUES.md` and on GitHub.

The repo started as a plan-only starter. Digital milestone M0 is implemented, one pull request per issue; M1 is next once M0 is closed.

## Order of work

1. Digital track, **milestone M0 first**. Do not start M1+ or the other tracks until M0 is done and CI is green.
2. After M0, finish digital milestones in order (M1, M2, M3) before spending serious effort on analog/RF or acoustic work, unless a later instruction from the owner says otherwise.
3. Analog/RF comes next (A0, then A1, …). Acoustic is a research/stretch track (S0, then S1, …) and must never be promised as a fab path.
4. Use plan mode at the start of each milestone. The plan must restate that milestone's acceptance criteria from `PLAN.md` / `ISSUES.md`.
5. **CI must be green before you move to the next milestone.** Do not leave a red main branch.

## License and tools

- The repository license is **Apache-2.0**. New source and example designs get SPDX headers: `Apache-2.0`.
- **Open-source tools only.** No Cadence, Synopsys, Siemens, or other commercial EDA, even as an optional extra.
- Call every EDA tool as an **external process** (subprocess or `docker run`). Never vendor tool source or binaries into this repo. Never `import` or link GPL-licensed libraries (including the KLayout Python module) into our Apache-2.0 code.
- GPL and LGPL tools are listed in `THIRD_PARTY_LICENSES.md`. If you add a tool, verify its license from the project's official source, record it in that file, and follow the same subprocess rule.
- Do not copy code from repositories that have **no license**. That includes [ssql2014/mcp4eda](https://github.com/ssql2014/mcp4eda). You may read public docs or papers for ideas; you may not paste or adapt their code.
- Pin the toolchain in a Docker image (`ghcr.io/rfingadam/siliconsmith-toolchain`). Do not require host installs besides Docker and Python.
- **Agent-written code runs only through the container runner.** Amaranth/MyHDL elaboration, cocotb tests, and later LibreLane configs are code the agent wrote. Run them only via the hardened `docker run` in `runners/docker.py` (`PLAN.md` section 5.1), never in the server process or a host subprocess. The server never imports amaranth, myhdl or cocotb. Never use `librelane --dockerized`.

## APIs that changed (most examples online are out of date)

- **MCP Python SDK v2** (`mcp>=2.3,<3`): `from mcp.server import MCPServer`. Report usage and toolchain errors by raising `ToolError` (from `mcp.server.mcpserver.exceptions`); never return an error string. Test with `from mcp import Client` (`async with Client(server) as c: await c.call_tool(...)`). Never use `mcp.server.fastmcp` or `FastMCP`; that is the v1 API and does not import on v2.
- **cocotb 2.x** (`cocotb==2.1.0`): the runner is `cocotb_tools.runner` (not `cocotb.runner`); `Clock(dut.clk, 10, unit="ns")` (not `units=`); read values with `int(dut.x.value)`. `BinaryValue`, `.integer` and `cocotb.fork` are gone (use `cocotb.start_soon`).
- CI fails on `FastMCP`, `BinaryValue`, `cocotb.runner` and `units=` in `src/**/*.py` and `src/siliconsmith/templates/**`. Skill text (`SKILL.md`) is not scanned, because the `cocotb2-tests` skill names these on purpose.

## Issues and CI

- Implement against the GitHub issues. When an issue's acceptance criteria are met, tick its boxes in `ISSUES.md` **and** in the GitHub issue body, then close the GitHub issue with a short comment pointing at the PR or commit.
- Keep labels (`track:…`, `milestone:…`) accurate. Do not add an `agent-approved` label.
- Extend `.github/workflows/ci.yml` as you go (ruff, mypy, tests, later synthesis/layout). Keep every `ci.yml` job green on `main`.

## Git and attribution

- Commits are authored as the repository owner, Adam Engelbrecht (`RFingAdam`).
- **No bot credit in commits or PRs.** Agents never add `Signed-off-by` lines (not even the owner's), `Co-authored-by` lines, "Generated with" lines, Cursor/Claude/Codex trailers, or any line that names an AI tool as an author or co-author. Outside contributors sign off their own commits (DCO).
- **CI trailer scan.** It fails on `Co-authored-by`, "Generated with" and AI-tool trailers in any commit of the PR or in the PR body. It fails on `Signed-off-by` only in PRs from a branch of this repository (the owner's and agents' PRs). In PRs from a fork, outside contributors' own `Signed-off-by` lines pass.
- Update a branch with `git merge origin/main`. Never rebase or force-push, and never use admin bypass.

## Product rules

- Never place an order, submit a shuttle, or pay for Tiny Tapeout (or anything else) automatically. Export a submission-ready repo; the owner submits by hand.
- Do not fake results, seed "passing" tests that do not test the design, or mark a milestone done if its acceptance criteria failed.
- Be honest in docs and skills: a 2.4 GHz Bluetooth radio is not realistic on a Tiny Tapeout analog tile; SAW/BAW filters have no open fab in this project.
- Keep secrets out of the repo and out of logs.
- Beginner-friendly docs: the owner is new to digital HDL. Example READMEs should explain what the design does and why, in plain English.
