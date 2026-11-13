# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The RTL text for the equivalence check: what is blanked out and what is refused."""

from __future__ import annotations

import re

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import equiv_sv as sv

# -- clean: comments and attributes go, nothing else moves --------------------------------------


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
    ],
)
def test_clean_blanks_comments_and_attributes(text: str, kept: str, gone: str) -> None:
    out = sv.clean(text)
    assert kept in out
    if gone:
        assert gone not in out
    assert len(out) == len(text) and out.count("\n") == text.count("\n")


@pytest.mark.parametrize(
    "event",
    ["@(*)", "@( * )", "@(* )", "@ (*)", "@(*\n)", "@(* /*c*/ )", "@/*c*/(*)", "@*", "@ *"],
)
def test_the_event_star_is_not_an_attribute(event: str) -> None:
    """`@(*)` in all its spellings is the event list, not the start of an attribute."""
    text = f"always {event} q = d; // ok\n  x = y; // *) (* z\n"
    clean, _, problem = sv.lex(text)
    assert problem is None
    cut = text.index("// ok")
    assert clean[:cut] == text[:cut] or clean[:cut].replace(" ", "") == text[:cut].replace(
        " ", ""
    ).replace("/*c*/", "")
    assert "q = d;" in clean and "x = y;" in clean


def test_a_product_in_parentheses_is_not_an_attribute() -> None:
    assert sv.clean("a = b * (c);  x*(y)\n") == "a = b * (c);  x*(y)\n"


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
    assert sv.clean(text).splitlines().index("b;") == text.splitlines().index("b;")


# -- lex: what it cannot read safely is a problem -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "a; /* never closed\n b;\n",
        "(* nosync reg r;\n",  # an attribute that is never closed
        "(* a; b *) reg r;\n",  # an attribute with `;`
        "(* a = `X *) reg r;\n",  # with a backtick
        "(* a (* b *) reg r;\n",  # with a second (*
        "(* a // c\n *) reg r;\n",
        "(* a /* c */ *) reg r;\n",
        'initial $display("abc\n"); (* nosync *) reg r;\n',  # a raw line break in a string
        'initial $display("abc',  # a string that is never closed
    ],
)
def test_what_cannot_be_read_safely_is_a_problem(text: str) -> None:
    assert sv.lex(text)[2] is not None
    found = sv.refusal([("d.v", text)])
    assert found is not None and found.code == "sim_synth"


def test_a_string_with_an_escaped_quote_or_a_star_paren_is_fine() -> None:
    assert sv.lex('$display("a\\"b", "*) (*");\n')[2] is None
    assert sv.lex('(* s = "a;b" *) reg r;\n')[2] is None  # `;` inside a string of an attribute


# -- refusal helpers ----------------------------------------------------------------------------


def ok(text: str, hand: bool = True) -> None:
    found = sv.refusal([("d.v", text)], None if hand else ())
    assert found is None, found


def refused(text: str, what: str, hand: bool = True) -> sv.Refusal:
    found = sv.refusal([("d.v", f"module m;\n{text}\nendmodule\n")], None if hand else ())
    assert found is not None, text
    assert what in found.what, (found, what)
    return found


# -- macros: every directive but four is refused ------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "`timescale 1ns/1ps",
        "`default_nettype none",
        "`default_nettype wire",
        "`resetall",
        "`celldefine",
        "`endcelldefine",
    ],
)
def test_the_four_plain_directives_are_accepted(line: str) -> None:
    ok(f"{line}\nmodule m; endmodule\n")


@pytest.mark.parametrize(
    "line",
    [
        "`define W 8",
        "`define F(a) a",
        "`undef W",
        "`ifdef W",
        "`ifndef W",
        "`elsif W",
        "`else",
        "`endif",
        '`line 1 "a.v" 0',
        "`pragma protect",
        '`begin_keywords "1800-2012"',
        "`end_keywords",
        "`__FILE__",
        "`W",
        "`NOT_DEFINED",
        "wire a``b;",
        "wire a ` b;",
    ],
)
def test_every_other_directive_and_any_macro_use_is_refused(line: str) -> None:
    found = sv.refusal([("d.v", f"module m;\n{line}\nendmodule\n")])
    assert found is not None and found.code == "sim_synth" and found.line in (2, 3)
    assert "localparam" in sv.plain_message(found) or "``" in found.what or "`" in found.what


def test_include_keeps_its_own_code_and_message() -> None:
    found = sv.refusal([("d.v", 'module m;\n  `include "x.vh"\nendmodule\n')])
    assert found is not None and found.code == "include" and found.line == 2
    assert "`include" in sv.plain_message(found)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("lpnosync", "`define LP (\nmodule m; `LP* nosync *) reg [7:0] r; endmodule\n"),
        ("argnosync", "`define AT(x) (x nosync x)\nmodule m; `AT(*) reg [7:0] r; endmodule\n"),
        ("stnosync", "`define ST *\nmodule m; (`ST nosync `ST) reg [7:0] r; endmodule\n"),
        ("slxoff", "`define SL /\nmodule m; `SL/ synopsys translate_off\nendmodule\n"),
        ("lpfullcase", "`define LP (\nmodule m; `LP* full_case *) case (s) endcase endmodule\n"),
        ("inactdef", "`ifdef SYNTHESIS\nwire a;\n`else\nwire b;\n`endif\nmodule m; endmodule\n"),
        ("bodydef", "`define A 1\n`ifdef A\nwire a;\n`endif\nmodule m; endmodule\n"),
        ("macgaten", "`define G and\nmodule m; `G #(5) g1 (w, a, b); endmodule\n"),
        ("quoteq", 'module m; `define Q "\n  initial $display(`Q "); // c\nendmodule\n'),
        ("idslash", "`define ID(x) x\nmodule m; `ID(/)`ID(/) synopsys translate_off\nendmodule\n"),
    ],
)
def test_the_reviewers_macro_designs_are_refused(name: str, text: str) -> None:
    found = sv.refusal([("d.v", text)])
    assert found is not None, name
    # a macro that builds an attribute is stopped by the tokenizer, the others by the macro rule
    assert found.code == "sim_synth"
    assert any(w in found.what for w in ("macro or directive", "attribute", "string"))


# -- keywords and types -------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["unique", "unique0", "priority", "primitive", "casex"])
def test_the_synthesis_assuming_keywords_are_refused(word: str) -> None:
    found = refused(f"always @* {word} /* c */ case (s)\nendcase", f"`{word}`")
    assert found.line == 2
    ok("wire priority_hi, unique_id, my_priority, casex_i;\n")  # only the whole word


@pytest.mark.parametrize("word", ["bit", "byte", "shortint", "int", "longint"])
def test_two_state_types_are_refused_with_the_reason(word: str) -> None:
    found = refused(f"  {word} [3:0] x;", f"`{word}`")
    assert "reg" in sv.plain_message(found) and "reset" in sv.plain_message(found)
    ok(f"wire {word}_x, x_{word}; integer i; reg r; logic l;\n")


def test_an_enum_needs_a_four_state_base_type() -> None:
    refused("typedef enum {IDLE, RUN} st_t;", "enum")  # cases8/enumreg: an int underneath
    refused("typedef enum bit [1:0] {A, B} t;", "enum")
    ok("typedef enum logic [1:0] {A, B, C} st_t;\n")
    ok("typedef enum reg [1:0] {A, B} st_t;\n")
    ok("typedef enum integer {A, B} st_t;\n")
    ok("typedef enum logic signed [2:0] {A, B} st_t;\n")


@pytest.mark.parametrize("op", ["===", "!=="])
def test_case_equality_is_refused(op: str) -> None:
    refused(f"assign o = r {op} 1'b1;", op)
    ok("assign o = (a == b) != (c != d);\n")


# -- x and z literals ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lit",
    ["2'bx1", "4'b1x0", "'hx", "8'hxF", "8 'h x", "3'o7x", "'sbx", "4'sb1X", "1'bX", "'x", "'X"],
)
def test_an_x_digit_is_refused_everywhere(lit: str) -> None:
    refused(f"always @* if (a == {lit}) y = 1;", "x")  # cases6/ifx
    refused(f"always @* casez (a)\n  {lit}: y = 1;\n  default: y = 0;\nendcase", "x")


@pytest.mark.parametrize("lit", ["1'bz", "4'b1z0", "'hz", "2'b1?", "8'h?F", "'z", "'Z"])
def test_z_and_question_marks_are_refused_outside_a_casez(lit: str) -> None:
    refused(f"assign o = en ? {lit} : 1'b0;", "z")
    refused(f"always @* case (a)\n  {lit}: y = 1;\n  default: y = 0;\nendcase", "z")


def test_wildcards_are_fine_inside_a_casez() -> None:
    ok(
        "module m; always @* begin\n  casez (a)\n    2'b1?: y = 1;\n    2'b?1: y = 2;\n"
        "    2'bzz: y = 3;\n    default: y = 0;\n  endcase\nend endmodule\n"
    )
    # a plain case nested in a casez is still inside it; a casez that ended is not
    refused("always @* begin casez (a) 2'b1?: y = 1; default: y = 0; endcase\n y = 2'b1?; end", "?")


def test_numbers_without_x_or_z_are_fine() -> None:
    ok("assign o = 8'hFF ^ 8'hde ^ 4'b1010 ^ 'd9 ^ 3'o7 ^ 12'sh7ff ^ 1'b0;\n")
    ok("wire [7:0] x_ok, z_ok, xz;\n")  # names that hold x or z


def test_the_reviewers_x_designs_are_refused() -> None:
    refused("always @* if (ui_in[1:0] == 2'bx1) y = 8'd1; else y = 8'd2;", "x digit")  # ifx
    refused("always @* casex (ui_in[1:0]) 2'b1x: y = 8'd1; default: y = 8'd0; endcase", "casex")
    refused("always @* casez (ui_in[1:0]) 2'b1x: y = 8'd1; default: y = 8'd0; endcase", "x digit")
    refused("assign uo_out = {7'd0, r === 1'bx};", "===")  # eqx and eqxdeep


# -- delays -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "assign #1 y = a;",
        "always @(posedge clk) q <= #1 d;",
        "initial begin #5 a = 1; end",
        "initial begin #0 a = 1; end",
        "initial begin #d a = 1; end",
        "always @(a) #(2) b = a;",
        "assign #(1, 2) y = a;",
        "and #2 g (y, a, b);",
        "and #(1) (o, a, b);",
        "always @* ##1 b = a;",
        "always @(posedge clk) if (a) #3 q = d;",
        "always @(posedge clk) begin\n case (a) 1'b0: q <= d; endcase #(12) q <= ~d; end",
        "wire signed #(5) w = a;",
        "wire t #(1) w;",
        "wire #(5) w = a;",
        "pullup #(2) (w);",
    ],
)
def test_delays_are_refused(text: str) -> None:
    refused(text, "delay")


def test_the_reviewers_delay_designs_are_refused() -> None:
    refused(
        "reg [7:0] q; always @(posedge clk) begin case (a) 1'b0: q <= d; endcase #(12) q <= d; end",
        "delay",
    )  # endcasedlyn
    refused("wire signed #(5) w = ui_in[0];", "delay")  # signeddly
    refused("wire w; and #(5) g1 (w, a, b);", "delay")  # macgaten, with the macro gone


@pytest.mark.parametrize(
    "text",
    [
        "module m #(parameter W = 8) (input clk); endmodule",
        "module m # (parameter W = 8) (input clk); endmodule",
        "module  m\n  #(\n parameter W = 8\n) ();\nendmodule",
        "module automatic m #(parameter W = 8) (); endmodule",
        "macromodule m #(parameter W = 8) (); endmodule",
        "module m; sub #(.W(8)) u_sub (.a(a)); endmodule",
        "module m; sub #(8) u_sub (.a(a)); endmodule",
        "module m; sub #(8, 4) u_sub (.a(a)); endmodule",
        "module m; sub #(.W(f(8))) u_sub (.a(a)); endmodule",
        "module m; sub #(8) u[3:0] (.a(a)); endmodule",
        "module m; sub #(8) u[3:0][1:0] (.a(a)); endmodule",
        "module m; sub\n  #(.W(8))\n  u_sub\n  (.a(a)); endmodule",
    ],
)
def test_a_parameter_list_is_not_a_delay(text: str) -> None:
    ok(text + "\n")


def test_a_hash_in_a_string_a_comment_or_an_escaped_name_is_fine() -> None:
    ok('module m;\n  initial $display("# not a delay");\n  // #5 comment\n  /* #6 */\nendmodule\n')
    ok("wire \\int_#5 ;\n")


# -- system tasks -------------------------------------------------------------------------------


@pytest.mark.parametrize("task", ["$readmemh", "$readmemb", "$test$plusargs", "$value$plusargs"])
def test_file_and_option_system_tasks_are_refused(task: str) -> None:
    refused(f'initial {task}("x", m);', task)


# -- verilator lint_off -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code",
    [
        "BLKSEQ", "BLKANDNBLK", "COMBDLY", "CASEX", "CASEWITHX", "CASEINCOMPLETE", "CASEOVERLAP",
        "LATCH", "NOLATCH", "MULTIDRIVEN", "SYNCASYNCNET", "INITIALDLY", "ALWCOMBORDER",
        "IMPLICITSTATIC",
    ],
)  # fmt: skip
@pytest.mark.parametrize("style", ["// verilator lint_off {c}", "/* verilator lint_off {c} */"])
def test_lint_off_of_the_races_and_x_warnings_is_refused(code: str, style: str) -> None:
    found = refused(f"  {style.format(c=code)}\n  wire a;", "lint_off")
    assert found.line == 2 and code in found.what  # the line of the comment, in the original


def test_lint_off_with_no_code_or_a_rule_flag_is_judged_too() -> None:
    refused("// verilator lint_off\nwire a;", "lint_off")
    refused("/* verilator lint_off */\nwire a;", "lint_off")
    refused("// verilator lint_off -rule BLKSEQ\nwire a;", "BLKSEQ")


@pytest.mark.parametrize(
    "code",
    ["UNUSEDSIGNAL", "UNUSEDPARAM", "WIDTH", "WIDTHEXPAND", "WIDTHTRUNC", "PINCONNECTEMPTY",
     "DECLFILENAME", "UNUSED", "VARHIDDEN"],
)  # fmt: skip
def test_other_lint_codes_may_be_switched_off(code: str) -> None:
    ok(
        f"module m;\n  // verilator lint_off {code}\n  wire a;\n"
        f"  // verilator lint_on {code}\nendmodule\n"
    )


def test_lint_on_and_other_comments_are_fine() -> None:
    ok("// verilator lint_on BLKSEQ\n// verilator public\nmodule m; endmodule\n")


def test_the_reviewers_race_designs_need_the_lint_warning_to_stay() -> None:
    race = "reg [7:0] a, b;\nalways @(posedge clk) a = ui_in;\nalways @(posedge clk) b = a;\n"
    ok(f"module m;\n{race}endmodule\n")  # the race itself is the lint step's to catch
    refused("// verilator lint_off BLKSEQ\n" + race, "BLKSEQ")  # cases4/race, cases7/racen


# -- hand-written Verilog only ------------------------------------------------------------------


@pytest.mark.parametrize(
    "event",
    ["@(a or b)", "@(a, b)", "@(ui_in[0])", "@ a", "@a", "@(posedge clk or b)", "@(a iff b)",
     "@(posedge clk iff en)"],
)  # fmt: skip
def test_an_event_list_with_a_level_signal_is_refused_in_hand_written_verilog(event: str) -> None:
    found = refused(f"always {event} y = a;", "event control")
    assert "always @(*)" in sv.plain_message(found)
    refused_less = sv.refusal([("core.v", f"module m;\nalways {event} y = a;\nendmodule\n")], ())
    assert refused_less is None  # generated Verilog (MyHDL) is not judged on this


@pytest.mark.parametrize(
    "event",
    [
        "@*",
        "@(*)",
        "@( * )",
        "@(posedge clk)",
        "@(negedge rst_n)",
        "@(posedge clk or negedge rst_n)",
        "@(posedge clk, negedge rst_n)",
        "@ (posedge clk or posedge a or negedge b)",
        "@(posedge clk[0])",
        "@(posedge (a & b))",
    ],
)
def test_star_and_edge_event_lists_are_fine(event: str) -> None:
    ok(f"module m;\nalways {event} q <= d;\nendmodule\n")


def test_the_reviewers_sensitivity_design_is_refused() -> None:
    refused("reg [7:0] cnt, y;\nalways @(ui_in[0]) y = cnt;", "event control")  # cases7/sensn


def test_myhdl_style_event_lists_are_not_judged() -> None:
    text = "module m;\nalways @(a, b, c) begin\n  y <= a & b;\nend\nendmodule\n"
    assert sv.refusal([("core.v", text)], ()) is None
    assert sv.refusal([("core.v", text)], {"core.v"}) is not None


@pytest.mark.parametrize("word", ["function", "task"])
def test_a_plain_function_or_task_is_refused_in_hand_written_verilog(word: str) -> None:
    refused(f"{word} [7:0] f;\n input [7:0] a;\n f = a;\n end{word}", "automatic")
    ok(f"module m; {word} automatic [7:0] f; input [7:0] a; f = a; end{word} endmodule\n")
    assert sv.refusal([("c.v", f"module m;\n{word} f; endmodule\n")], ()) is None


def test_the_reviewers_function_design_is_refused() -> None:
    refused(  # cases7/fstaticn: a simulator keeps `keep` between calls
        "function [7:0] f(input [7:0] a);\n reg [7:0] keep;\n begin\n if (a[0]) keep = a;\n"
        " f = keep;\n end\nendfunction\nassign uo_out = f(ui_in);",
        "automatic",
    )


# -- documented limits: not detected, and pinned here -------------------------------------------


def test_an_out_of_range_read_is_not_detected() -> None:
    """cases7/oorif2: `v[ui_in[2:0]]` with an index past the width is x in a simulator and some
    value in the gates. The check does not see it (documented): gate-level simulation does."""
    ok(
        "module m;\nwire [5:0] v = ui_in[7:2];\nreg [7:0] y;\nalways @* begin\n"
        "  if (!v[ui_in[2:0]]) y = 8'd1;\n  else y = 8'd2;\nend\nendmodule\n"
    )


def test_a_division_by_zero_is_not_detected() -> None:
    ok("module m;\nassign q = a / b;\nendmodule\n")


# -- generated Verilog and ordinary hand-written RTL are not refused ----------------------------


def test_ordinary_rtl_is_accepted() -> None:
    ok(
        "`timescale 1ns/1ps\n`default_nettype none\nmodule m #(parameter W = 8) (input clk);\n"
        "  localparam [1:0] IDLE = 2'd0, RUN = 2'd1;\n  reg [W-1:0] q; integer i;\n"
        '  (* keep *) reg k;\n  (* keep,\n     src = "m.v:1" *) reg k2;\n'
        "  // verilator lint_off UNUSEDSIGNAL\n  sub #(.W(4)) u (.clk(clk));\n"
        "  always @(posedge clk) q <= q + 1'b1;\n  always @(*) i = 0;\n"
        "  function automatic [3:0] f(input [3:0] a); f = a; endfunction\n"
        "  typedef enum logic [1:0] {A, B} st_t;\nendmodule\n`default_nettype wire\n"
    )


def test_every_construct_of_the_myhdl_front_end_is_accepted() -> None:
    myhdl = (
        "// SPDX-License-Identifier: Apache-2.0\n// Generated by MyHDL 0.11.51\n"
        "`timescale 1ns/10ps\n\nmodule counter (\n  input clk,\n  input rst_n,\n"
        "  output reg [7:0] uo_out\n);\n"
        "// verilator lint_off UNUSEDSIGNAL\nreg [7:0] count;\n\n"
        "always @(posedge clk, negedge rst_n) begin: COUNTER_SEQ\n"
        "  if (rst_n == 0) begin\n    count <= 0;\n  end else begin\n    count <= (count + 1);\n"
        "  end\nend\n\n"
        "always @(count, rst_n) begin: COUNTER_COMB\n  uo_out = count;\nend\n\nendmodule\n"
    )
    assert sv.refusal([("counter.v", myhdl)], ()) is None


def test_the_first_refusal_names_file_and_line() -> None:
    found = sv.refusal([("project.v", "wire a;\n"), ("core.v", "\n\n  bit z;\n")])
    assert found is not None and (found.file, found.line) == ("core.v", 3)
    assert "core.v, line 3" in sv.plain_message(found)


def test_every_message_has_a_plain_hint() -> None:
    for text in (
        "`define A 1",
        "bit b;",
        "typedef enum {A} t;",
        "unique case (a) endcase",
        "assign #1 y = a;",
        "assign y = a === b;",
        "assign y = 2'bx0;",
        "// verilator lint_off BLKSEQ",
        "always @(a) y = a;",
        "function f; endfunction",
        '$readmemh("f", m);',
    ):
        found = sv.refusal([("d.v", f"module m;\n{text}\nendmodule\n")])
        assert found is not None, text
        message = sv.plain_message(found)
        assert not message.endswith("differently, so the check cannot vouch for it. Remove it."), (
            text
        )


@pytest.mark.parametrize("front_end", ["verilog", "myhdl", "amaranth"])
def test_the_recorded_demo_designs_are_not_refused(front_end: str) -> None:
    """The Verilog the three recorded demo sessions wrote or emitted (docs/demos): the hand-written
    core of the Verilog demo is judged by every rule, the generated files by the common ones."""
    text = (REPO_ROOT / "docs" / "demos" / f"{front_end}-20261112-1.md").read_text()
    blocks = re.findall(r"```verilog\n(.*?)```", text, re.S)
    assert len(blocks) >= 2
    for block in blocks:
        hand = {"demo.v"} if front_end == "verilog" else set()
        assert sv.refusal([("demo.v", block)], hand) is None
