# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Result envelope returned by every tool.

The summary is what the agent reads first, so it is capped at 20 lines and 2,000 characters.
Anything longer goes into `details`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

MAX_SUMMARY_LINES = 20
MAX_SUMMARY_CHARS = 2000
MAX_LINE_CHARS = 500


def _overflow_line(hidden: int) -> str:
    return f"… ({hidden} more lines in details)"


def make_summary(lines: list[str]) -> str:
    """Join `lines` into a summary within the line and character caps.

    If the text is too long, trailing lines are replaced by one final line:
    `… (N more lines in details)`. A single line over 500 characters is cut with a trailing `…`.
    """
    flat: list[str] = []
    for line in lines:
        for part in line.splitlines() or [""]:
            if len(part) > MAX_LINE_CHARS:
                part = part[: MAX_LINE_CHARS - 1] + "…"
            flat.append(part)
    text = "\n".join(flat)
    if len(flat) <= MAX_SUMMARY_LINES and len(text) <= MAX_SUMMARY_CHARS:
        return text
    for keep in range(min(len(flat), MAX_SUMMARY_LINES - 1), -1, -1):
        candidate = "\n".join([*flat[:keep], _overflow_line(len(flat) - keep)])
        if len(candidate) <= MAX_SUMMARY_CHARS:
            return candidate
    raise AssertionError("unreachable: the overflow line alone is under the cap")


def summary_within_limits(summary: str) -> bool:
    return len(summary.splitlines()) <= MAX_SUMMARY_LINES and len(summary) <= MAX_SUMMARY_CHARS


class Artifact(BaseModel):
    path: str
    kind: str


class ToolResult(BaseModel):
    ok: bool
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    next_hints: list[str] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def _summary_not_too_long(cls, value: str) -> str:
        if not summary_within_limits(value):
            raise ValueError(
                f"summary exceeds {MAX_SUMMARY_LINES} lines or {MAX_SUMMARY_CHARS} characters; "
                "build it with make_summary() or ToolResult.from_lines()"
            )
        return value

    @classmethod
    def from_lines(
        cls,
        ok: bool,
        lines: list[str],
        *,
        details: dict[str, Any] | None = None,
        artifacts: list[Artifact] | None = None,
        next_hints: list[str] | None = None,
    ) -> ToolResult:
        """Build a result whose summary is capped by `make_summary`."""
        return cls(
            ok=ok,
            summary=make_summary(lines),
            details=details or {},
            artifacts=artifacts or [],
            next_hints=next_hints or [],
        )
