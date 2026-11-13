# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The RTL as a simulator reads it: comment and attribute removal, and what is refused."""

from __future__ import annotations

import pytest

from siliconsmith.worker import equiv_sv as sv

# -- clean: comments and attributes go, nothing else moves ---------------------------------------


@pytest.mark.parametrize(
    ("text", "kept", "gone"),
    [
        ("a = b; // synopsys translate_off\nc = d;\n", "c = d;", "translate_off"),
        ("a = b; /* synopsys full_case */ c = d;\n", "c = d;", "full_case"),
        ("/* multi\nline\ncomment */ x = y;\n", "x = y;", "comment"),
        ("a; // one /* not a block\nb; /* still\nopen */ c;\n", "b;", "block"),
        ("a; /* a // line inside */ b;\n", "b;", "inside"),
        ('$display("a // b /* c");\n', '"a // b /* c"', ""),  # comments inside a string stay
        ('x = "q\\"//"; // c\n', '"q\\"//"', " c"),  # an escaped quote does not end the string
        ("\\a//b  c;\n", "\\a//b", ""),  # an escaped identifier may hold `//`
        ("(* nosync *) reg r;\n", "reg r;", "nosync"),
        ('(* keep,\n   src = "a.v:1.1-2.2" *) reg r;\n', "reg r;", "keep"),
        ('(* note = "has *) inside" *) reg r;\n', "reg r;", "inside"),
        ("module m (input (* x *) a, output b);\n", "input", "x *"),
        ("(* full_case *) case (s)\n", "case (s)", "full_case"),
        ("always @* begin\n", "always @* begin", ""),
    ],
)
def test_clean_blanks_comments_and_attributes(text: str, kept: str, gone: str) -> None:
    out = sv.clean(text)
    assert kept in out
    if gone:
        assert gone not in out
    assert len(out) == len(text) and out.count("\n") == text.count("\n")


@pytest.mark.parametrize("text", ["@(*)", "@( * )", "@(* )", "@ (*)", "a = b * (c);", "x*(y)"])
def test_the_star_of_a_sensitivity_list_or_a_product_is_not_an_attribute(text: str) -> None:
    assert sv.clean(f"always {text} q = d;\n") == f"always {text} q = d;\n"


def test_an_attribute_on_a_port_an_instance_and_a_statement() -> None:
    text = (
        "module m ((* mark *) input a, output (* x = 1 *) b);\n"
        "  (* keep *) sub (* inst *) u (.a(a));\n"
        "  always @(*) (* parallel_case *) case (a) 1'b0: b = 0; endcase\n"
        "endmodule\n"
    )
    out = sv.clean(text)
    for word in ("mark", "x = 1", "keep", "inst", "parallel_case"):
        assert word not in out
    assert "always @(*)" in out and "case (a)" in out and "sub" in out


def test_clean_keeps_every_line_number() -> None:
    text = "a;\n// c1\n/* c2\nc3 */\n(* x,\ny *)\nb;\n"
    out = sv.clean(text)
    assert out.splitlines().index("b;") == text.splitlines().index("b;")


# -- refusal: what is let through --------------------------------------------------------------


def ok(text: str, name: str = "d.v") -> None:
    assert sv.refusal([(name, text)]) is None, sv.refusal([(name, text)])


def test_ordinary_rtl_is_accepted() -> None:
    ok(
        "`timescale 1ns/1ps\n`default_nettype none\nmodule m #(parameter W = 8) (input clk);\n"
        "  reg [W-1:0] q; integer i;\n  sub #(.W(4)) u (.clk(clk));\n"
        "  always @(posedge clk) q <= q + 1'b1;\n  always @(*) i = 0;\nendmodule\n"
        "`default_nettype wire\n"
    )


def test_a_macro_defined_in_the_files_may_be_tested_and_used() -> None:
    ok("`define WIDTH 8\n`ifdef WIDTH\nwire [`WIDTH-1:0] w;\n`else\nwire v;\n`endif\n")
    assert sv.refusal([("a.v", "`define X\n"), ("b.v", "`ifdef X\n`endif\n")]) is None
    found = sv.refusal([("a.v", "`define X\n`undef X\n`ifdef X\n`endif\n")])
    assert found is not None and "`ifdef X" in found.what


@pytest.mark.parametrize(
    "macro",
    ["SYNTHESIS", "YOSYS", "FORMAL", "__ICARUS__", "VERILATOR", "COCOTB_SIM", "PDK_sky130A"],
)
@pytest.mark.parametrize("word", ["ifdef", "ifndef", "elsif"])
def test_a_macro_the_files_did_not_define_is_refused(macro: str, word: str) -> None:
    found = sv.refusal([("d.v", f"module m;\n`{word} {macro}\n`endif\nendmodule\n")])
    assert found is not None and found.code == "sim_synth"
    assert found.what == f"`{word} {macro}" and found.line == 2


def test_include_is_its_own_refusal() -> None:
    found = sv.refusal([("d.v", 'module m;\n  `include "x.vh"\nendmodule\n')])
    assert found is not None and found.code == "include" and found.line == 2
    assert "`include" in sv.plain_message(found)


@pytest.mark.parametrize(
    "text",
    [
        "`pragma protect\n",
        '`line 1 "a.v" 0\n',
        '`begin_keywords "1800-2012"\n',
        "`__FILE__\n",
        "`NOT_DEFINED\n",
        "wire a``b;\n",
        "wire a ` b;\n",
    ],
)
def test_other_directives_and_token_pasting_are_refused(text: str) -> None:
    assert sv.refusal([("d.v", text)]) is not None


@pytest.mark.parametrize("word", ["unique", "unique0", "priority"])
def test_the_synthesis_assuming_keywords_are_refused(word: str) -> None:
    found = sv.refusal([("d.v", f"always @* {word} /* c */ case (s)\nendcase\n")])
    assert found is not None and f"`{word}`" in found.what
    ok("wire priority_hi, unique_id, my_priority;\n")  # only the whole word


@pytest.mark.parametrize("word", ["bit", "byte", "shortint", "int", "longint"])
def test_two_state_types_are_refused_with_the_reason(word: str) -> None:
    found = sv.refusal([("d.v", f"module m;\n  {word} [3:0] x;\nendmodule\n")])
    assert found is not None and f"`{word}`" in found.what
    assert "reg" in sv.plain_message(found) and "reset" in sv.plain_message(found)
    ok(f"wire {word}_x, x_{word}; integer i; reg r; logic l;\n")


@pytest.mark.parametrize(
    "text",
    [
        "assign #1 y = a;",
        "always @(posedge clk) q <= #1 d;",
        "initial begin #5 a = 1; end",
        "always @(a) #(2) b = a;",
        "assign #(1, 2) y = a;",
        "and #2 g (y, a, b);",
        "always @* ##1 b = a;",
        "always @(posedge clk) if (a) #3 q = d;",
        "`define D #1\nassign `D y = a;",
        "`define D #(1)\nassign `D y = a;",
    ],
)
def test_delays_are_refused(text: str) -> None:
    found = sv.refusal([("d.v", f"module m;\n{text}\nendmodule\n")])
    assert found is not None and "delay" in found.what


def test_a_hash_that_starts_a_parameter_list_or_sits_in_a_string_is_fine() -> None:
    ok("module m #(parameter W = 1) (input a);\n  sub #(.W(2)) u (.a(a));\nendmodule\n")
    ok('module m;\n  initial $display("# not a delay");\n  // #5 comment\n  /* #6 */\nendmodule\n')
    ok("module m #(\n  parameter A = 1\n) ();\nendmodule\n")


@pytest.mark.parametrize("task", ["$readmemh", "$readmemb", "$test$plusargs", "$value$plusargs"])
def test_file_and_option_system_tasks_are_refused(task: str) -> None:
    found = sv.refusal([("d.v", f'initial {task}("x", m);\n')])
    assert found is not None and task in found.what


def test_what_sits_in_a_comment_attribute_or_string_is_not_refused() -> None:
    ok("// `ifdef SYNTHESIS\n/* bit x; #5 unique */\n(* bit = 1 *) wire w;\n")
    ok('initial $display("`include int #5 $readmemh");\n')
    ok("wire \\int_#5 ;\n")  # an escaped identifier is a name


def test_the_first_refusal_names_file_and_line() -> None:
    found = sv.refusal([("project.v", "wire a;\n"), ("core.v", "\n\n  bit z;\n")])
    assert found is not None and (found.file, found.line) == ("core.v", 3)
    assert "core.v, line 3" in sv.plain_message(found)
