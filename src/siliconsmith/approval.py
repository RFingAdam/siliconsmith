# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`siliconsmith approve <dir>`: the owner records that spec.yaml says what they want.

This is a command-line command only. No MCP tool approves a spec. It refuses unless both stdin
and stdout are terminals. That is an honest-use convention, not a security boundary: an agent
that has its own shell and a pseudo-terminal could still run it. The `chip-design-loop` skill
tells agents never to, and the status table says who approved and when.
"""

from __future__ import annotations

import getpass
import hashlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.ledger import Approval, now_utc, set_approval
from siliconsmith.paths import project_lock, safe_read
from siliconsmith.project import Project
from siliconsmith.spec import MAX_SPEC_BYTES, SPEC_NAME, Spec, parse_spec

CONFIRM_WORD = "approve"

CONVENTION = (
    "This records your name and the spec's hash in .siliconsmith/ledger.json. It is an "
    "honest-use convention, not a security boundary: the AI agent is told never to run this "
    "command, and the ledger shows who approved what, but nothing technical stops someone "
    "with a terminal from running it."
)


REFUSAL = (
    "Refusing to approve: this command must be run by a person at a terminal (both input and "
    "output must be a terminal). Approval is the owner's step."
)


def have_terminal(stdin: TextIO, stdout: TextIO) -> bool:
    return bool(stdin.isatty() and stdout.isatty())


def git_user_name(project_dir: Path) -> str:
    """`git config user.name` for the project, else the operating-system user name."""
    try:
        proc = subprocess.run(
            ["git", "config", "user.name"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        name = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        name = ""
    if name:
        return name
    try:
        return getpass.getuser()
    except Exception:  # no login name in this environment
        return "unknown"


def _read_valid_spec(project: Project) -> tuple[Spec, str]:
    try:
        raw = safe_read(project.dir, SPEC_NAME, MAX_SPEC_BYTES)
    except ToolError as exc:
        raise ToolError(f"cannot read spec.yaml: {exc}") from exc
    try:
        parsed = parse_spec(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ToolError("spec.yaml is not valid UTF-8 text") from exc
    if not isinstance(parsed, Spec):
        shown = "; ".join(f"{e.path}: {e.message}" for e in parsed[:5])
        raise ToolError(f"spec.yaml is not valid, so it cannot be approved: {shown}")
    return parsed, hashlib.sha256(raw).hexdigest()


def approve_project(
    project: Project,
    *,
    stdin: TextIO,
    stdout: TextIO,
    ask: Callable[[str], str] = input,
) -> int:
    """Run the approval conversation. Returns the exit code (0 only when approved)."""

    def say(text: str = "") -> None:
        print(text, file=stdout)

    if not have_terminal(stdin, stdout):
        say(REFUSAL)
        return 1
    try:
        spec, sha = _read_valid_spec(project)
    except ToolError as exc:
        say(f"Refusing to approve: {exc}")
        return 1
    say(f"Spec for {project.name}: {spec.title}")
    say(spec.summary)
    say("Requirements:")
    for req in spec.requirements:
        say(f"  {req.id}: {req.text}")
    say(f"spec.yaml sha256: {sha}")
    say()
    say(CONVENTION)
    answer = ask(f"Type '{CONFIRM_WORD}' to approve this spec (anything else cancels): ")
    if answer.strip() != CONFIRM_WORD:
        say("Not approved. Nothing was recorded.")
        return 1
    who = git_user_name(project.dir)
    with project_lock(project.dir):
        _, now_sha = _read_valid_spec(project)
        if now_sha != sha:
            say("spec.yaml changed while you were reading it. Nothing was recorded; run again.")
            return 1
        set_approval(project.dir, Approval(spec_sha256=sha, approved_by=who, at=now_utc()))
    say(f"Approved by {who}. Run `siliconsmith status {project.dir}` to refresh the report.")
    return 0
