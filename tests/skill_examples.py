# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Find the runnable code examples in the skills.

Convention: a fenced block whose info string is `<lang> example=<id> project=<name>
frontend=<fe> file=<path>` is one file of a runnable example. Blocks with the same `example` id
(in any skill) are written into one project. Every example is checked with `emit_verilog`. If it
has files under `test/`, it is also run on both simulators. A block may add `tests=<other id>` to
use the `test/` files of another example (the counters share one test file).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "src" / "siliconsmith" / "skills"
FENCE = re.compile(r"^```(\w+) ([^\n]*example=[^\n]*)\n(.*?)^```$", re.S | re.M)


@dataclass
class Example:
    id: str
    project: str
    frontend: str
    files: dict[str, str] = field(default_factory=dict)
    tests_from: str | None = None
    skills: set[str] = field(default_factory=set)


def parse_examples() -> dict[str, Example]:
    examples: dict[str, Example] = {}
    for skill in sorted(SKILLS.glob("*/SKILL.md")):
        for match in FENCE.finditer(skill.read_text(encoding="utf-8")):
            attrs = dict(word.split("=", 1) for word in shlex.split(match.group(2)))
            ex = examples.setdefault(
                attrs["example"], Example(attrs["example"], attrs["project"], attrs["frontend"])
            )
            assert (ex.project, ex.frontend) == (attrs["project"], attrs["frontend"]), ex.id
            assert attrs["file"] not in ex.files, f"{ex.id}: {attrs['file']} appears twice"
            ex.files[attrs["file"]] = match.group(3)
            ex.skills.add(skill.parent.name)
            if "tests" in attrs:
                ex.tests_from = attrs["tests"]
    return examples
