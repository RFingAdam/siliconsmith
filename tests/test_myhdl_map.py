# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Mapping a line of MyHDL's generated Verilog back to the agent's hdl/ function."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import myhdl_map

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "synth"


def _project(tmp_path: Path, source: str) -> Path:
    (tmp_path / "hdl").mkdir()
    (tmp_path / "hdl" / "counter.py").write_text((FIXTURES / source).read_text())
    return tmp_path


# Real MyHDL 0.11 output (captured in the toolchain image):
#   always @(count, ui_in) begin: remember          line 39 of myhdl_latch_core.v (the `if`)
#   always @(gate, count) begin: keeper0_remember   line 44 of myhdl_nested_core.v
LATCH_CORE = (FIXTURES / "myhdl_latch_core.v").read_text()
NESTED_CORE = (FIXTURES / "myhdl_nested_core.v").read_text()


def test_block_label_finds_the_enclosing_always_block() -> None:
    assert myhdl_map.block_label(NESTED_CORE, 46) == "keeper0_remember"
    assert myhdl_map.block_label(NESTED_CORE, 44) == "keeper0_remember"
    assert myhdl_map.block_label(NESTED_CORE, 30) == "seq"
    assert myhdl_map.block_label(LATCH_CORE, 40) == "remember"


def test_block_label_is_none_outside_every_block() -> None:
    assert myhdl_map.block_label(NESTED_CORE, 41) is None  # the `assign gate = ...` line
    assert myhdl_map.block_label(NESTED_CORE, 52) is None
    assert myhdl_map.block_label(NESTED_CORE, 1) is None


def test_top_block_function_with_the_signal_it_assigns(tmp_path: Path) -> None:
    work = _project(tmp_path, "myhdl_latch.py")
    mapped = myhdl_map.map_line(LATCH_CORE, 39, "counter", "held", work)
    assert mapped == {"file": "hdl/counter.py", "line": 19, "function": "remember"}


def test_function_of_a_sub_block_uses_its_only_assignment(tmp_path: Path) -> None:
    work = _project(tmp_path, "myhdl_nested.py")
    # The label is keeper0_remember; `held` is called `q` inside keeper.
    mapped = myhdl_map.map_line(NESTED_CORE, 46, "counter", "held", work)
    assert mapped == {"file": "hdl/counter.py", "line": 11, "function": "remember"}


def test_without_a_signal_a_function_with_two_assignments_gives_its_def_line(
    tmp_path: Path,
) -> None:
    work = tmp_path
    (work / "hdl").mkdir()
    (work / "hdl" / "counter.py").write_text(
        "from myhdl import block, always_comb\n"
        "\n"
        "@block\n"
        "def counter(a, b, c):\n"
        "    @always_comb\n"
        "    def logic():\n"
        "        b.next = a\n"
        "        c.next = a\n"
        "    return logic\n"
    )
    core = "always @(a) begin: logic\n    b = a;\n    c = a;\nend\n"
    assert myhdl_map.map_line(core, 2, "counter", "", work) == {
        "file": "hdl/counter.py",
        "line": 6,
        "function": "logic",
    }


def test_a_label_that_matches_no_function_is_not_mapped(tmp_path: Path) -> None:
    work = _project(tmp_path, "myhdl_latch.py")
    core = "always @(a) begin: nosuch\n    b = a;\nend\n"
    assert myhdl_map.map_line(core, 2, "counter", "b", work) is None


def test_the_block_named_by_the_label_prefix_wins_between_equal_names(tmp_path: Path) -> None:
    (tmp_path / "hdl").mkdir()
    (tmp_path / "hdl" / "counter.py").write_text(
        "from myhdl import block, always_comb\n"
        "\n"
        "@block\n"
        "def first(a, b):\n"
        "    @always_comb\n"
        "    def logic():\n"
        "        b.next = a\n"
        "    return logic\n"
        "\n"
        "@block\n"
        "def second(a, b):\n"
        "    @always_comb\n"
        "    def logic():\n"
        "        b.next = ~a\n"
        "    return logic\n"
    )
    core = "always @(a) begin: second0_logic\n    b = ~a;\nend\n"
    mapped = myhdl_map.map_line(core, 2, "counter", "b", tmp_path)
    assert mapped is not None and mapped["line"] == 14


def test_annotate_adds_fields_and_names_the_hdl_file_in_the_message(tmp_path: Path) -> None:
    work = _project(tmp_path, "myhdl_latch.py")
    findings = [
        {
            "file": "src/counter.v",
            "line": 39,
            "msg": "Latch inferred for signal 'tt_um_counter.core.held'",
            "signal": "held",
        },
        {"file": "src/project.v", "line": 3, "msg": "elsewhere", "signal": ""},
    ]
    myhdl_map.annotate(findings, LATCH_CORE, "counter", work)
    first, second = findings
    assert (first["hdl_file"], first["hdl_line"], first["hdl_function"]) == (
        "hdl/counter.py",
        19,
        "remember",
    )
    assert first["msg"].endswith("(hdl/counter.py:19, function remember)")
    assert "hdl_file" not in second and second["msg"] == "elsewhere"


def test_the_python_files_are_parsed_never_run(tmp_path: Path) -> None:
    (tmp_path / "hdl").mkdir()
    marker = tmp_path / "ran"
    (tmp_path / "hdl" / "counter.py").write_text(
        f"open({str(marker)!r}, 'w').write('x')\ndef counter():\n    def f():\n        pass\n"
    )
    functions = myhdl_map.read_functions(tmp_path)
    assert [f.name for f in functions] == ["counter", "f"]
    assert not marker.exists()


@pytest.mark.parametrize("text", ["def broken(:\n", "\x00"])
def test_unparsable_files_are_skipped(tmp_path: Path, text: str) -> None:
    (tmp_path / "hdl").mkdir()
    (tmp_path / "hdl" / "counter.py").write_text(text)
    assert myhdl_map.read_functions(tmp_path) == []


# -- level-sensitive processes (the equivalence check refuses them) -------------------------------


def _level(tmp_path: Path, body: str) -> dict[str, object] | None:
    (tmp_path / "hdl").mkdir(exist_ok=True)
    (tmp_path / "hdl" / "d.py").write_text(
        "from myhdl import *\n\n@block\ndef top(clk, a, b, y, cnt):\n"
        + body
        + "\n    return pick\n"
    )
    return myhdl_map.level_sensitive(myhdl_map.read_functions(tmp_path))


@pytest.mark.parametrize(
    ("decorator", "line_body"),
    [
        ("@always(b)", "        y.next = cnt"),  # the reviewer's mysens2
        ("@always(a, b)", "        y.next = cnt"),
        ("@always(clk.posedge, b)", "        y.next = cnt"),
        ("@always(a.negedge, b.val)", "        y.next = cnt"),
        ("@myhdl.always(b)", "        y.next = cnt"),
        ("@always()", "        y.next = cnt"),
        ("@always", "        y.next = cnt"),
    ],
)
def test_an_always_that_waits_for_a_level_is_found(
    tmp_path: Path, decorator: str, line_body: str
) -> None:
    found = _level(tmp_path, f"    {decorator}\n    def pick():\n{line_body}\n")
    assert found is not None
    assert (found["file"], found["function"], found["line"]) == ("hdl/d.py", "pick", 5)
    assert "level" in str(found["what"]) or "always" in str(found["what"])


@pytest.mark.parametrize(
    "yielded",
    ["a", "a, b", "delay(5)", "clk.posedge, b", "a.posedge, delay(1)", "None", ""],
)
def test_an_instance_that_yields_a_level_is_found(tmp_path: Path, yielded: str) -> None:
    body = f"    @instance\n    def pick():\n        while True:\n            yield {yielded}\n"
    found = _level(tmp_path, body)
    assert found is not None and found["function"] == "pick" and found["line"] == 8


@pytest.mark.parametrize(
    "body",
    [
        "    @always_comb\n    def pick():\n        y.next = cnt\n",
        "    @always_seq(clk.posedge, reset=a)\n    def pick():\n        y.next = cnt\n",
        "    @always(clk.posedge)\n    def pick():\n        y.next = cnt\n",
        "    @always(clk.posedge, a.negedge)\n    def pick():\n        y.next = cnt\n",
        "    @instance\n    def pick():\n        while True:\n            yield clk.posedge\n",
        "    @instance\n    def pick():\n        while True:\n"
        "            yield clk.posedge, a.negedge\n",
        "    @block\n    def sub():\n        pass\n    pick = sub\n",
    ],
)
def test_the_complete_forms_are_not_found(tmp_path: Path, body: str) -> None:
    assert _level(tmp_path, body) is None


def test_the_reviewers_mysens2_source_is_found() -> None:
    path = REPO_ROOT / "tests" / "fixtures" / "equiv_designs" / "mysens2" / "myhdl.py"
    source = path.read_text()
    functions = [
        myhdl_map.Function("hdl/mysens2.py", n.name, n.lineno, "mysens2", n)
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name != "mysens2"
    ]
    found = myhdl_map.level_sensitive(functions)
    decorator_line = source.splitlines().index("    @always(b0)") + 1
    assert found is not None and found["function"] == "pick" and found["line"] == decorator_line
