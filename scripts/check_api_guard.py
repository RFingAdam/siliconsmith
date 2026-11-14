# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fail if code uses APIs that were removed in the MCP SDK v2 or cocotb 2.

Scans git-tracked `src/**/*.py` and `src/siliconsmith/templates/**`. Skill prose is not scanned,
because the skills name the banned APIs on purpose.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

# The first name is split so this file does not contain the literal string.
PATTERN = re.compile(r"Fast" r"MCP|BinaryValue|cocotb\.runner\b|\bunits\s*=")
PATHSPECS = ("src/**/*.py", "src/siliconsmith/templates/**")


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", *PATHSPECS],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    return [root / name.decode() for name in out.split(b"\0") if name]


def scan(paths: Iterable[Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            match = PATTERN.search(line)
            if match:
                problems.append(f"{path}:{number}: banned API '{match.group(0)}'")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    problems = scan(tracked_files(root))
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} stale API use(s) found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
