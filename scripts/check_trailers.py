# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fail if commit messages or the PR body carry attribution trailers.

Usage: python scripts/check_trailers.py [BASE]

BASE (or env BASE) is the commit to compare against; messages come from
`git log --format=%B BASE..HEAD`. The PR body, if any, is read from env PR_BODY.

Signed-off-by lines are rejected on same-repo branches (owner and agents) and accepted on fork
PRs; the CLA (see check_cla.py), not the DCO, covers outside contributors. A PR is from a fork
when env HEAD_REPO and BASE_REPO are both set and differ.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Mapping

COAUTHOR = re.compile(r"^co-authored-by\s*:", re.IGNORECASE)
SIGNOFF = re.compile(r"^signed-off-by\s*:", re.IGNORECASE)
# "Generated with [Claude Code]" style footers, with or without a leading emoji.
BOT_FOOTER = re.compile(r"^\W*generated (with|by)\b", re.IGNORECASE)
# Any "<something>-by:" trailer that names an AI tool.
AI_TRAILER = re.compile(
    r"^[\w-]+-by\s*:.*\b(claude|cursor|codex|copilot|chatgpt|gemini|aider|devin)\b",
    re.IGNORECASE,
)


def offending_lines(text: str, allow_signoff: bool = False) -> list[str]:
    """Return trailer-shaped lines that are not allowed.

    Only lines that start like a trailer or bot footer match, so prose that mentions these
    words is fine. Sign-off lines are allowed when `allow_signoff` is true (PRs from forks).
    """
    bad = []
    for line in text.splitlines():
        if SIGNOFF.match(line):
            if not allow_signoff:
                bad.append(line)
        elif COAUTHOR.match(line) or BOT_FOOTER.match(line) or AI_TRAILER.match(line):
            bad.append(line)
    return bad


def is_fork_pr(env: Mapping[str, str]) -> bool:
    head = env.get("HEAD_REPO", "")
    base = env.get("BASE_REPO", "")
    return bool(head and base and head != base)


def main(argv: list[str]) -> int:
    base = argv[1] if len(argv) > 1 else os.environ.get("BASE", "")
    sources: list[tuple[str, str]] = []
    if base:
        log = subprocess.run(
            ["git", "log", "--format=%B", f"{base}..HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        sources.append((f"commit messages {base}..HEAD", log))
    body = os.environ.get("PR_BODY", "")
    if body:
        sources.append(("PR body", body))
    if not sources:
        print("no BASE and no PR_BODY given; nothing to check", file=sys.stderr)
        return 2
    allow_signoff = is_fork_pr(os.environ)
    failed = False
    for label, text in sources:
        for line in offending_lines(text, allow_signoff):
            print(f"{label}: {line}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
