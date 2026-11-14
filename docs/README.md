# Siliconsmith documentation

## Suggested reading order for a newcomer

1. [The top-level README](../README.md): what Siliconsmith is, install, and how to connect your
   agent.
2. [Beginner guide](beginner-guide.md): the design loop in plain English, what each check proves
   and does not prove, synthesis and gate-level simulation explained, and your own steps
   (approving the spec, and later submitting by hand).
3. [Glossary](glossary.md): 42 terms with RF comparisons. Keep it open alongside the beginner
   guide and the demos. The `explain` tool serves the same text to your agent.
4. [Recorded demos](demos/README.md): real agent sessions, start to finish, with how they are
   isolated and verified and a list of every attempt.
5. Your own project's `docs/verification.md`: the validation table that `status` writes. The
   beginner guide explains how to read it.

## All documents

| Document | What it is for |
| --- | --- |
| [Beginner guide](beginner-guide.md) | For engineers who know RF and analog hardware and are new to digital design. |
| [Glossary](glossary.md) | Plain-English meanings of the chip-design and Tiny Tapeout terms Siliconsmith uses. |
| [Recorded demos](demos/README.md) | Headless Claude Code sessions that design a counter with only Siliconsmith's tools. |
| [PLAN.md](../PLAN.md) | The architecture, the [threat model and sandbox](../PLAN.md#51-threat-model-and-sandbox), the [validation sequence](../PLAN.md#57-validation-sequence), and every milestone with its acceptance criteria. |
| [ISSUES.md](../ISSUES.md) | Every issue, with acceptance boxes that are ticked as they are met. |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | Dev setup, the checks CI runs, and the license rules and the CLA. |
| [SECURITY.md](../SECURITY.md) | How to report a vulnerability privately, and what is in and out of scope. |
| [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md) | Every open-source tool used or planned, its verified license, and how it is run. |
| [NOTICE](../NOTICE) | Copyright and attributions. |
| [CLAUDE.md](../CLAUDE.md) | Rules for AI agents and anyone implementing a milestone in this repository. |

## The skills the agent reads

The agent gets its guidance from five skills, served through the `explain` tool. You can read
them here too:

- [chip-design-loop](../src/siliconsmith/skills/chip-design-loop/SKILL.md): the whole loop. The
  agent reads this first.
- [tt-rules](../src/siliconsmith/skills/tt-rules/SKILL.md): Tiny Tapeout's pins, the core and
  wrapper split, size and clock limits, and honest limits.
- [amaranth-style](../src/siliconsmith/skills/amaranth-style/SKILL.md): writing designs in
  Amaranth 0.5.
- [myhdl-style](../src/siliconsmith/skills/myhdl-style/SKILL.md): writing designs in MyHDL 0.11.
- [cocotb2-tests](../src/siliconsmith/skills/cocotb2-tests/SKILL.md): writing cocotb 2.1 tests,
  including how to convert cocotb 1.x examples.
