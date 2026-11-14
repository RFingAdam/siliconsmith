# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import pytest
from pydantic import ValidationError

from siliconsmith.result import Artifact, ToolResult, make_summary


def test_short_summary_unchanged() -> None:
    assert make_summary(["a", "b"]) == "a\nb"


def test_summary_caps_lines() -> None:
    out = make_summary([f"line {i}" for i in range(25)])
    lines = out.splitlines()
    assert len(lines) == 20
    assert lines[:19] == [f"line {i}" for i in range(19)]
    assert lines[-1] == "… (6 more lines in details)"


def test_summary_caps_chars() -> None:
    out = make_summary(["x" * 100 for _ in range(50)])
    assert len(out) <= 2000
    assert len(out.splitlines()) <= 20
    assert out.splitlines()[-1].endswith("more lines in details)")


def test_summary_single_huge_line() -> None:
    out = make_summary(["y" * 5000])
    assert len(out) <= 2000


def test_summary_exactly_at_limits_is_kept() -> None:
    lines = [str(i) for i in range(20)]
    assert make_summary(lines) == "\n".join(lines)


def test_model_rejects_long_summary() -> None:
    with pytest.raises(ValidationError):
        ToolResult(ok=True, summary="\n".join(["x"] * 21))
    with pytest.raises(ValidationError):
        ToolResult(ok=True, summary="z" * 2001)


def test_from_lines_always_valid() -> None:
    result = ToolResult.from_lines(False, [f"e{i}" for i in range(100)])
    assert not result.ok
    assert len(result.summary.splitlines()) <= 20


def test_defaults_and_artifacts() -> None:
    result = ToolResult(ok=True, summary="done", artifacts=[Artifact(path="a.v", kind="verilog")])
    assert result.details == {}
    assert result.next_hints == []
    assert result.artifacts[0].kind == "verilog"
