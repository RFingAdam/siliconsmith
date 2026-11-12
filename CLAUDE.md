# CLAUDE.md — rules for work in this repository

You are implementing Siliconsmith. Read this file and `PLAN.md` before you write a plan or any code. Then use plan mode **once per milestone**, get the plan approved, and only then build that milestone.

## What this project is

Siliconsmith is an Apache-2.0 MCP server plus a skill pack and a pinned Docker toolchain. It lets an AI coding agent design small chips with open-source tools only. There are three tracks (digital, analog/RF, acoustic filters) that share one MCP server, one toolchain image, and one agent loop. Details, architecture, and acceptance criteria are in `PLAN.md`. Issues are in `ISSUES.md` and on GitHub.

This repo starts as a plan-only starter. You will add the implementation.

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

## Issues and CI

- Implement against the GitHub issues. When a issue's acceptance criteria are met, update `ISSUES.md` and close the GitHub issue with a short comment pointing at the PR or commit.
- Keep labels (`track:…`, `milestone:…`) accurate. Do not add an `agent-approved` label.
- Extend `.github/workflows/ci.yml` as you go (ruff, mypy, tests, later synthesis/layout). The placeholder job must keep passing until you replace it with real checks.

## Git and attribution

- Commits are authored as the repository owner, Adam Engelbrecht (`RFingAdam`).
- **No bot credit in commits.** Do not add `Co-Authored-By`, `Signed-off-by` for a bot, Cursor/Claude/Codex trailers, or any line that names an AI tool as a commit author or co-author.
- Do not force-push, rebase published history, or use admin bypass.

## Product rules

- Never place an order, submit a shuttle, or pay for Tiny Tapeout (or anything else) automatically. Export a submission-ready repo; the owner submits by hand.
- Do not fake results, seed "passing" tests that do not test the design, or mark a milestone done if its acceptance criteria failed.
- Be honest in docs and skills: a 2.4 GHz Bluetooth radio is not realistic on a Tiny Tapeout analog tile; SAW/BAW filters have no open fab in this project.
- Keep secrets out of the repo and out of logs.
- Beginner-friendly docs: the owner is new to digital HDL. Example READMEs should explain what the design does and why, in plain English.
