# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Python error extractor (it runs in the container; stdlib only, so it runs here too)."""

from __future__ import annotations

import ast
import linecache
from typing import Any

import pytest

from siliconsmith.worker.pyerror import design_error, extract, format_lines, map_paths

DESIGN = "/work/hdl/chip.py"
SOURCE = "from amaranth.hdl import Module\n\n\ndef top():\n    return undefined_name\n"


@pytest.fixture(autouse=True)
def _source_in_linecache() -> Any:
    """Make linecache know a file that does not exist on the host."""
    linecache.cache[DESIGN] = (len(SOURCE), None, SOURCE.splitlines(True), DESIGN)
    yield
    linecache.cache.pop(DESIGN, None)


def raised(code: str, filename: str = DESIGN) -> BaseException:
    try:
        exec(compile(code, filename, "exec"), {})
    except BaseException as exc:
        return exc
    raise AssertionError("did not raise")


def test_syntax_error_has_file_line_column_and_source() -> None:
    exc = raised("def top(:\n    pass\n")
    assert isinstance(exc, SyntaxError)
    error = extract(exc, "hdl/chip.py")
    assert error["kind"] == "SyntaxError"
    assert (error["file"], error["line"], error["col"]) == ("hdl/chip.py", 1, 9)
    assert error["msg"] == "SyntaxError: invalid syntax"
    assert error["source"] == "def top(:"


def test_host_ast_parse_gives_the_same_shape() -> None:
    """The host checks syntax with ast.parse before it starts a container."""
    with pytest.raises(SyntaxError) as info:
        ast.parse("x = (1,\n", filename="hdl/chip.py")
    error = extract(info.value, "hdl/chip.py")
    assert error["file"] == "hdl/chip.py"
    assert error["line"] == 1
    assert "never closed" in error["msg"]


def test_indentation_error_is_a_syntax_error() -> None:
    error = extract(raised("if True:\npass\n"), "hdl/chip.py")
    assert error["kind"] == "IndentationError"
    assert error["line"] == 2


def test_exception_uses_the_deepest_frame_under_hdl() -> None:
    code = "def helper():\n    return 1 / 0\n\n\ndef top():\n    return helper()\n\ntop()\n"
    error = extract(raised(code), "hdl/chip.py")
    assert error["kind"] == "ZeroDivisionError"
    assert (error["file"], error["line"]) == ("hdl/chip.py", 2)
    assert error["msg"] == "ZeroDivisionError: division by zero"


def test_frames_outside_hdl_are_skipped() -> None:
    # The design's frame is the deepest one inside /work/hdl, even if library code raised it.
    code = "def top():\n    return int('x')\n\ntop()\n"
    error = extract(raised(code), "hdl/chip.py")
    assert error["kind"] == "ValueError"
    assert (error["file"], error["line"]) == ("hdl/chip.py", 2)


def test_source_line_comes_from_the_file() -> None:
    # Line 5 of the registered SOURCE holds `return undefined_name`.
    error = extract(raised("\n\n\n\nundefined_name\n"), "hdl/chip.py")
    assert error["line"] == 5
    assert error["source"] == "return undefined_name"


@pytest.mark.parametrize(
    ("message", "file", "line"),
    [
        ("in file /work/hdl/chip.py, line 12:\n  Local variable referenced", "hdl/chip.py", 12),
        ("see /work/hdl/chip.py:7 for the cause", "hdl/chip.py", 7),
        ('File "/work/hdl/chip.py", line 3, in elaborate', "hdl/chip.py", 3),
        # A file under /work/hdl wins over an earlier library path.
        ("/opt/venv/x.py:99 then /work/hdl/chip.py:4", "hdl/chip.py", 4),
    ],
)
def test_location_from_the_message_when_no_frame_is_ours(
    message: str, file: str, line: int
) -> None:
    error = extract(RuntimeError(message), "hdl/other.py")
    assert (error["file"], error["line"]) == (file, line)
    assert "/work" not in error["msg"]


def test_no_location_falls_back_to_the_entry_file() -> None:
    error = extract(RuntimeError("boom"), "hdl/chip.py")
    assert (error["file"], error["line"], error["source"]) == ("hdl/chip.py", None, None)
    assert error["msg"] == "RuntimeError: boom"


def test_long_messages_are_cut() -> None:
    error = extract(RuntimeError("\n".join(f"line {n}" for n in range(100))), "hdl/chip.py")
    assert len(error["msg"].splitlines()) <= 8
    error = extract(RuntimeError("x" * 5000), "hdl/chip.py")
    assert len(error["msg"]) <= 600


def test_design_error_names_the_file() -> None:
    error = design_error("hdl/chip.py", "hdl/chip.py must define top()")
    assert error["kind"] == "DesignError"
    assert error["file"] == "hdl/chip.py" and error["line"] is None


def test_format_lines_are_short_and_use_project_paths() -> None:
    exc = RuntimeError("one\ntwo\nin file /work/hdl/chip.py, line 12:\nthree\nfour")
    lines = format_lines(extract(exc, "hdl/chip.py"))
    assert lines[0].startswith("hdl/chip.py:12: RuntimeError: one")
    assert len(lines) <= 8
    assert all("/work" not in line for line in lines)


def test_map_paths() -> None:
    assert map_paths("/work/src/a.v and /work/hdl/b.py") == "src/a.v and hdl/b.py"
