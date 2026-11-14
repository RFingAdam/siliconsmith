# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`equiv_elab`: does Verilator's elaboration hold every block that Yosys elaborates?

Small hand-written RTLIL texts and trees in the shape of the real ones (a Yosys process is
preceded by attribute lines, one of them `src`; Verilator's nodes have a `type`, a `loc` of the
form `<file letter>,<line>:<col>,<line>:<col>`, and for an ALWAYS a `keyword`).
"""

from __future__ import annotations

from typing import Any

import pytest

from siliconsmith.worker import equiv_elab as ee
from siliconsmith.worker import equiv_tree as et

META = {"files": {"e": {"filename": "core.v"}, "f": {"filename": "project.v"}}}


def il(*sources: str) -> str:
    """RTLIL with one process per `src` value."""
    body = "".join(
        f'  attribute \\src "{s}"\n  process $proc$x{i}\n    assign \\a \\b\n  end\n'
        for i, s in enumerate(sources)
    )
    return f'module \\top\n  attribute \\src "core.v:1.1-9.9"\n  wire \\a\n{body}end\n'


def tree(*nodes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "NETLIST", "modulesp": list(nodes)}


def block(line: int, col: int, keyword: str = "always", file: str = "e") -> dict[str, Any]:
    return {"type": "ALWAYS", "keyword": keyword, "loc": f"{file},{line}:{col},{line}:{col + 6}"}


def call(
    line: int, col: int, name: str = "f", dotted: str = "", kind: str = "FUNCREF", file: str = "e"
) -> dict[str, Any]:
    node = {"type": kind, "name": name, "loc": f"{file},{line}:{col},{line}:{col + 1}"}
    return node | ({"dotted": dotted} if dotted else {})


# -- processes -----------------------------------------------------------------------------------


def test_processes_reads_the_src_of_every_process() -> None:
    assert ee.processes(il("core.v:3.3-5.6", "project.v:11.16-11.21")) == [
        ee.Process("core.v", 3, 3, 5, 6),
        ee.Process("project.v", 11, 16, 11, 21),
    ]


def test_all_attribute_lines_before_a_process_belong_to_it() -> None:
    text = (
        'module \\m\n  attribute \\nosync 1\n  attribute \\src "core.v:4.3-4.9"\n'
        "  attribute \\keep 1\n  process $p\n  end\n"
        '  attribute \\src "core.v:9.9-9.12"\n  wire \\w\n'  # a wire's src is not a process's
        "  process $q\n  end\nend\n"
    )
    with pytest.raises(ee.ElabError):  # $q follows a wire: its attributes do not carry over
        ee.processes(text)
    assert ee.processes(text.split("  process $q")[0]) == [ee.Process("core.v", 4, 3, 4, 9)]


def test_a_src_with_several_parts_gives_every_part() -> None:
    assert ee.processes(il("core.v:3.3-3.9|project.v:5.1-5.4")) == [
        ee.Process("core.v", 3, 3, 3, 9),
        ee.Process("project.v", 5, 1, 5, 4),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "module \\m\n  process $p\n  end\nend\n",  # no src at all
        'module \\m\n  attribute \\src "garbage"\n  process $p\n  end\nend\n',
        'module \\m\n  attribute \\src "core.v:3.3-3.9|junk"\n  process $p\n  end\nend\n',
    ],
)
def test_a_process_without_a_readable_src_fails_closed(text: str) -> None:
    with pytest.raises(ee.ElabError):
        ee.processes(text)


def test_an_rtlil_without_a_process_has_no_places() -> None:
    assert ee.processes("module \\m\n  wire \\a\nend\n") == []


# -- the anchor rule -----------------------------------------------------------------------------


def test_always_and_always_ff_are_anchored_at_the_keyword() -> None:
    text = (
        "module m;\n  always @(posedge clk) q <= d;\n  always_ff @(posedge clk) r <= d;\nendmodule"
    )
    assert ee.anchor(text, 2, 3) == ee.Anchor("always", "always", 2, 3)
    assert ee.anchor(text, 3, 3) == ee.Anchor("always", "always_ff", 3, 3)


@pytest.mark.parametrize("word", ["always_comb", "always_latch"])
def test_always_comb_and_latch_are_anchored_just_after_the_keyword(word: str) -> None:
    text = f"module m;\n  {word} s = sq(a);\nendmodule"
    after = 3 + len(word)  # Yosys: the zero-width spot right after the keyword
    assert ee.anchor(text, 2, after) == ee.Anchor("always", word, 2, 3)


def test_initial_is_skipped_and_a_call_is_anchored_at_its_name() -> None:
    text = "module m;\n  initial x = 0;\n  assign z = top_f(a);\nendmodule"
    assert ee.anchor(text, 2, 10) == ee.Anchor("skip", "initial", 2, 3)
    assert ee.anchor(text, 3, 14) == ee.Anchor("call", "top_f", 3, 14)


def test_a_declared_name_with_a_value_is_a_declaration_and_its_calls_are_listed() -> None:
    text = (
        "module m;\n  reg [7:0] x = f(a) + g (b), y = h(c);\n  reg \\$e  = 0;\n"
        "  wire w = $clog2(3);\nendmodule"
    )
    found = ee.anchor(text, 2, 13)
    assert found == ee.Anchor("decl", "x", 2, 13)
    assert [(c.word, c.line, c.col) for c in ee.initializer_calls(text, found)] == [
        ("f", 2, 17),
        ("g", 2, 24),
    ]
    assert ee.anchor(text, 3, 7) == ee.Anchor("decl", "\\$e", 3, 7)
    assert ee.initializer_calls(text, ee.Anchor("decl", "\\$e", 3, 7)) == []
    assert ee.anchor(text, 4, 8) == ee.Anchor("decl", "w", 4, 8)
    assert ee.initializer_calls(text, ee.Anchor("decl", "w", 4, 8)) == []  # a system function


def test_a_comparison_after_a_name_is_not_a_declaration() -> None:
    assert ee.anchor("assign z = f(a) == 1;", 1, 12) == ee.Anchor("call", "f", 1, 12)


def test_an_escaped_identifier_is_a_call_and_is_found_from_both_sides() -> None:
    text = "assign z = \\my.f (a);"
    assert ee.anchor(text, 1, 12) == ee.Anchor("call", "\\my.f", 1, 12)
    assert ee.anchor(text, 1, 17) == ee.Anchor("call", "\\my.f", 1, 12)


def test_a_tab_counts_as_one_column() -> None:
    text = "module m;\n\talways @* y = ~a;\n\tassign z = f(a);\nendmodule"
    assert ee.anchor(text, 2, 2) == ee.Anchor("always", "always", 2, 2)
    assert ee.anchor(text, 3, 13) == ee.Anchor("call", "f", 3, 13)


@pytest.mark.parametrize(("line", "col"), [(2, 1), (2, 2), (9, 1), (0, 1), (2, 0), (2, 40)])
def test_a_position_with_no_identifier_is_unplaceable(line: int, col: int) -> None:
    assert ee.anchor("module m;\n  (a + b);\nendmodule", line, col) is None


def test_a_position_in_the_middle_of_a_word_is_not_the_start_of_it() -> None:
    text = "assign z = abc(a);"
    assert ee.anchor(text, 1, 12) == ee.Anchor("call", "abc", 1, 12)
    assert ee.anchor(text, 1, 13) is None
    assert ee.anchor(text, 1, 14) is None


# -- unmatched -----------------------------------------------------------------------------------

CORE = (
    "module m;\n  always @* begin y = a; end\n  assign z = f(a);\n  always_comb q = a;\nendmodule\n"
)
CLEAN = {"core.v": CORE, "project.v": "module p;\nendmodule\n"}
HAVE = tree(block(2, 3), call(3, 14), block(4, 3, "always_comb"))
ALL = il("core.v:2.3-2.28", "core.v:3.14-3.15", "core.v:4.14-4.14")


def test_everything_matched_is_none() -> None:
    assert ee.unmatched(HAVE, META, ALL, CLEAN) is None


def test_an_unrelated_extra_node_or_no_process_is_none() -> None:
    assert ee.unmatched(tree(), META, il(), CLEAN) is None
    assert ee.unmatched(tree(block(2, 3), block(8, 3)), META, il("core.v:2.3-2.9"), CLEAN) is None


def test_a_declaration_with_a_value_needs_the_calls_in_it() -> None:
    text = {"core.v": "module m;\n  reg [7:0] x = f(a);\n  reg y = 0;\nendmodule\n"}
    rtlil = il("core.v:2.13-2.24", "core.v:3.7-3.12")
    assert ee.unmatched(tree(call(2, 17)), META, rtlil, text) is None
    got = ee.unmatched(tree(), META, rtlil, text)
    assert got is not None and "the call of `f` at core.v, line 2" in got


def test_initial_blocks_need_nothing() -> None:
    text = {"core.v": "module m;\n  initial x = 0;\nendmodule\n"}
    assert ee.unmatched(tree(), META, il("core.v:2.10-2.10"), text) is None


def test_a_missing_always_is_named_with_file_and_line() -> None:
    got = ee.unmatched(tree(call(3, 14), block(4, 3, "always_comb")), META, ALL, CLEAN)
    assert got is not None
    assert "Yosys keeps the always block at core.v, line 2" in got
    assert "1/0" in got and "Give the constant a defined value" in got


def test_a_one_assignment_block_that_verilator_lacks_is_a_difference() -> None:
    # no excuse for a plain block: `{t, y} = {cnt, t}` reads t before it writes it, and the
    # positions are the only thing that shows the block is missing (Verilator runs with -fno-dfg,
    # so it keeps such a block at its keyword)
    text = {
        "core.v": "module m;\n  always @(*) {t, y} = {cnt, t};\n  always @* y = 8'd0;\nendmodule\n"
    }
    for src in ("core.v:2.3-2.37", "core.v:3.3-3.21"):
        got = ee.unmatched(tree(), META, il(src), text)
        assert got is not None and "always block at core.v" in got


def test_an_always_comb_in_a_branch_only_yosys_keeps_is_found() -> None:
    text = {"core.v": "module m;\n  always_comb\\y = \\; ^ t;\nendmodule\n"}
    got = ee.unmatched(tree(), META, il("core.v:2.14-2.14"), text)
    assert got is not None and "always block at core.v, line 2" in got


def test_an_escaped_name_after_always_comb_is_not_a_declaration() -> None:
    text = "module m;\n  always_comb\\y = \\; ^ t;\nendmodule"
    assert ee.anchor(text, 2, 14, zero_width=True) == ee.Anchor("always", "always_comb", 2, 3)
    assert ee.anchor(text, 2, 14) == ee.Anchor("decl", "\\y", 2, 14)  # a real declaration


def test_a_declaration_value_scan_does_not_stop_inside_an_escaped_name() -> None:
    text = "module m;\n  wire \\;  = f(a);\nendmodule"
    found = ee.anchor(text, 2, 8)
    assert found == ee.Anchor("decl", "\\;", 2, 8)
    assert [c.word for c in ee.initializer_calls(text, found)] == ["f"]


def test_a_package_scoped_call_is_anchored_at_the_function_name() -> None:
    text = "module m;\n  assign z = pkg::mix(a, b);\nendmodule"
    assert ee.anchor(text, 2, 14) == ee.Anchor("call", "mix", 2, 19)


def test_an_array_element_is_no_call() -> None:
    # Yosys turns `assign q = taps[i];` into a process of its own, at the array name
    text = "module m;\n  assign q = taps [i];\nendmodule"
    assert ee.anchor(text, 2, 14) == ee.Anchor("other", "taps", 2, 14)
    both = ee.unmatched(tree(), META, il("core.v:2.14-2.22"), {"core.v": text})
    assert both is None


def test_a_hierarchical_call_is_still_a_call_for_the_position_check() -> None:
    text = "module m;\n  assign z = g.f(a);\nendmodule"
    assert ee.anchor(text, 2, 14) == ee.Anchor("call", "g", 2, 14)


def test_a_missing_call_is_named() -> None:
    got = ee.unmatched(tree(block(2, 3), block(4, 3, "always_comb")), META, ALL, CLEAN)
    assert got is not None and "the call of `f` at core.v, line 3" in got


def test_the_keyword_must_be_the_same() -> None:
    wrong = tree(block(2, 3), call(3, 14), block(4, 3, "always"))  # not always_comb
    got = ee.unmatched(wrong, META, ALL, CLEAN)
    assert got is not None and "core.v, line 4" in got


def test_a_call_node_at_another_column_does_not_match() -> None:
    got = ee.unmatched(tree(block(2, 3), call(3, 15), block(4, 3, "always_comb")), META, ALL, CLEAN)
    assert got is not None and "call of `f`" in got


def test_a_task_call_matches_a_call_process() -> None:
    have = tree(block(2, 3), call(3, 14, kind="TASKREF"), block(4, 3, "always_comb"))
    assert ee.unmatched(have, META, ALL, CLEAN) is None


def test_a_node_of_the_wrong_file_does_not_match() -> None:
    have = tree(block(2, 3, file="f"), call(3, 14), block(4, 3, "always_comb"))
    got = ee.unmatched(have, META, ALL, CLEAN)
    assert got is not None and "core.v, line 2" in got


def test_a_dotted_call_is_refused_even_when_everything_else_matches() -> None:
    have = tree(block(2, 3), call(3, 14, "f", "g"), block(4, 3, "always_comb"))
    got = ee.unmatched(have, META, ALL, CLEAN)
    assert got is not None and "hierarchical name (`g.f`, core.v, line 3)" in got


def test_a_dotted_call_in_a_file_that_is_not_ours_is_not_judged() -> None:
    have = tree(
        block(2, 3), call(3, 14), block(4, 3, "always_comb"), call(1, 1, "s", "std", file="z")
    )
    assert (
        ee.unmatched(have, {"files": {**META["files"], "z": {"filename": "std.sv"}}}, ALL, CLEAN)
        is None
    )


def test_a_position_that_cannot_be_placed_fails_closed() -> None:
    assert ee.unmatched(HAVE, META, il("core.v:1.1-1.5"), CLEAN) is None  # `module`: no kind
    got = ee.unmatched(HAVE, META, il("core.v:2.1-2.2"), CLEAN)  # blanks only
    assert got is not None and "cannot place a block that Yosys keeps (core.v, line 2)" in got


def test_a_process_of_another_file_fails_closed() -> None:
    got = ee.unmatched(HAVE, META, il("elsewhere.v:2.3-2.9"), CLEAN)
    assert got is not None and "cannot place a block that Yosys keeps (elsewhere.v, line 2)" in got


def test_the_first_problem_by_file_order_then_line_is_reported() -> None:
    clean = {"project.v": "module p;\n  always @* y = a;\nendmodule\n", "core.v": CORE}
    rtlil = il("core.v:2.3-2.9", "project.v:2.3-2.9", "core.v:3.14-3.15")
    got = ee.unmatched(tree(), META, rtlil, clean)  # nothing in the tree
    assert got is not None and "project.v, line 2" in got  # project.v comes first in `clean`
    got = ee.unmatched(tree(block(2, 3, file="f")), META, rtlil, clean)
    assert got is not None and "core.v, line 2" in got  # then core.v by line


def test_an_unreadable_input_raises() -> None:
    with pytest.raises(ee.ElabError):
        ee.unmatched(HAVE, META, "module \\m\n  process $p\n  end\nend\n", CLEAN)
    with pytest.raises(et.TreeError):
        ee.unmatched([], META, ALL, CLEAN)
    with pytest.raises(et.TreeError):
        ee.unmatched(HAVE, {}, ALL, CLEAN)
