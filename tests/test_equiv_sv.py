# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The RTL text for the equivalence check: what is blanked out and what is refused."""

from __future__ import annotations

import re
from pathlib import Path

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
    advice = sv.plain_message(found)
    assert "reg" in advice and "reset" in advice
    assert "for header" in advice and "integer" in advice  # what to write for a loop index
    ok(f"wire {word}_x, x_{word}; integer i; reg r; logic l;\n")


@pytest.mark.parametrize("word", ["bit", "byte", "shortint", "int", "longint"])
@pytest.mark.parametrize("sign", ["", " unsigned", " signed"])
def test_a_two_state_loop_index_declared_in_the_for_header_is_accepted(
    word: str, sign: str
) -> None:
    """A variable declared in a for header is automatic and the header sets it before every
    run of the loop: it never shows the 0 a two-state variable starts at (r15 ia20, ib17)."""
    index = f"{word}{sign} i = 0"
    ok(f"always_comb begin y = '0; for ({index}; i < 8; i++) y[i] = a[7-i]; end\n")
    split = f"(\n  {index};\n  i < 8;\n  i = i + 1)"  # the header over several lines
    ok(f"always @* begin\n y = 0;\n for {split}\n y[i] = a[i];\nend\n")
    ok(f"always_ff @(posedge clk) for ({index}; i < 4; i++) q[i] <= d[3-i];\n")
    ok(f"always @(posedge clk) begin for ({index}, j = 7; i < 4; i++) q[i] <= d[j]; end\n")
    ok(f"always_comb for ({index}, {word} j = 7; i < 4; i++) y[i] = d[j];\n")
    ok(  # a loop inside a function
        "function automatic [7:0] rev(input [7:0] v);\n"
        f"  for ({index}; i < 8; i++) rev[i] = v[7 - i];\n"
        "endfunction\n"
    )


@pytest.mark.parametrize(
    "text",
    [
        "int count;",  # a module-level variable: it starts at 0
        "bit r;\nalways @(posedge clk) r <= ~r;",  # a register
        "always_comb begin int i; for (i = 0; i < 8; i++) y[i] = a[i]; end",  # declared outside
        "function automatic [7:0] f(input [7:0] v); int k; f = v; endfunction",
        "function automatic int f(input [7:0] v); f = v; endfunction",  # the return type
        "function automatic [7:0] f(input int v); f = v; endfunction",  # an argument
        "always_comb for (i = int'(a); i < 8; i++) y[i] = 0;",  # a cast in the header
        "always_comb for (i = 0; i < 8; i++) begin int t; t = a; y[i] = t; end",  # in the body
        "always_comb for (i = 0; i < int'(n); i++) y[i] = 0;",  # the condition
        "always_comb for (i = 0; i < 8; i++) y[i] = byte'(a);",
        "always_comb for (int i = 0; i < 8; i++) begin bit b; b = a[i]; y[i] = b; end",
        "always_comb for (int i == 0; i < 8; i++) y[i] = 0;",  # not a declaration
        "always_comb for (int i; i < 8; i++) y[i] = 0;",  # no value from the header
        "always_comb for (j = f(a, int'(b)); j < 8; j++) y[j] = 0;",  # inside a call
        "always_comb for ((int i = 0); i < 8; i++) y[i] = 0;",
        "for (int i = 0; i < 8; i++) assign y[i] = a[i];\nint k;",  # another use stays refused
        "always_comb for (int i = 0; i < 8; i++) y[i] = 0;\nwire [7:0] t = int'(a);",
        # round 16: only the first clause of the header, only right after `(` or `,`, and only
        # a `for` header counts
        "always_comb for (i = 0; int k = 0; i++) y[i] = 0;",  # after the first `;`
        "always_comb for (i = 0; i < 8; i++, int k = 0) y[i] = 0;",  # in the step clause
        "always_comb for (static int i = 0; i < 8; i++) y[i] = 0;",  # after another word
        "always_comb for (automatic int i = 0; i < 8; i++) y[i] = 0;",
        "always_comb for (var int i = 0; i < 8; i++) y[i] = 0;",
        "always_comb foreach (int i = 0) y[i] = 0;",  # not a `for`
        "always_comb begin y = 0; xfor (int i = 0); end",
    ],
)
def test_a_two_state_type_anywhere_but_a_for_header_declaration_is_refused(text: str) -> None:
    refused(text, "two-state type")


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


@pytest.mark.parametrize(
    "lit",
    ["2147483648", "2_147_483_648", "4294967295", "3000000000", "4294967296", "'sd2147483648",
     "'d3000000000", "'hFFFFFFFF", "'sh80000000", "' sd 2147483648",
     "'b1_0000000_00000000_00000000_00000000"],
)  # fmt: skip
def test_an_unsized_number_of_2_to_the_31_or_more_is_refused(lit: str) -> None:
    """Round 13 w40bigfold/w42impfold: Verilator reads `2147483648` as a negative 32-bit value,
    Yosys and Icarus as a wider positive one, so `if (2147483648 > 0)` takes another branch in
    the tree the order and purity checks judge."""
    found = refused(f"always @* if ({lit} > 0) y = 1; else y = 0;", "unsized number")
    assert "Give it a size" in sv.plain_message(found)


@pytest.mark.parametrize(
    "lit", ["'sb1", "'shF", "'so7", "'sh8", "'Sh8", "'SB1", "'sH8000", "' sh 8", "'sh0_8", "'so4"]
)
def test_an_unsized_signed_binary_octal_or_hex_number_is_refused(lit: str) -> None:
    """Round 24 (b01, b04, sbloc, sbsig): Icarus 12 extends an unsized signed b/o/h number from its
    written digits ('sh8 is -8, 'sb1 -1), Verilator and Yosys pad it with zeros (+8, +1), and
    Verilator's tree shows it as the plain `8`, so a function's localparam of it passed as one
    number as written; in a signal (`ui_in + 'sh8`) the gates differ from the simulation too."""
    for text in (
        f"function automatic integer h(input integer a);\n  localparam M = {lit};\n"
        "  h = a + M;\nendfunction",
        f"assign o = {{24'd0, a}} + {lit};",
        f"generate if ({lit} < 0) begin : g1 end endgenerate",
    ):
        found = refused(text, f"the unsized signed number `{lit}`")
        message = sv.plain_message(found)
        assert (
            "Give it a size (`32'sh8`; `32'shF` is 15 and `4'shF` is -1 in every tool)" in message
        )
        assert "Icarus 12 extends an unsized signed binary, octal or hex number" in message


@pytest.mark.parametrize(
    "text",
    [
        "assign o = 'sd8 + 'Sd255 + 'sd2147483647 + ' sd 7;",  # a decimal: every tool agrees
        "assign o = 32'sh8 + 4'shF + 8 'sb1 + 6'so4 + 12'SH7ff;",  # sized
        "assign o = 'h8 + 'b1 + 'o7 + 'hF;",  # unsized but unsigned
    ],
)
def test_a_signed_decimal_and_a_sized_signed_number_are_fine(text: str) -> None:
    ok(f"module m;\n{text}\nendmodule\n")


@pytest.mark.parametrize(
    "lit",
    ["'h0_0000_000F", "'h000000000", "'H0_7FFF_FFFF", "'o00000000001", "'b0" + "0" * 32,
     "'b_0" + "0_" * 32 + "1"],
)  # fmt: skip
def test_an_unsized_number_whose_digits_need_more_than_32_bits_is_refused(lit: str) -> None:
    """Round 24 (b06, b07, longhex): Icarus and Verilator make `'h0_0000_000F` 32 bits wide,
    Yosys 36 (as many bits as its digits, leading zeros included), so `$bits(M)` of it is 32 in a
    simulator and 36 in the gates; Verilator's tree shows it as the plain 15."""
    found = refused(
        f"localparam M = {lit};\nassign o = $bits(M);",
        f"the unsized number `{lit}`, whose digits need more than 32 bits",
    )
    assert "Give it a size (`36'h0_0000_000F`) or drop the leading zeros" in sv.plain_message(found)


@pytest.mark.parametrize(
    "text",
    [
        "assign o = 'h0000_000F + 'hFFFF + 'o7777777777 + 'o0000000001;",  # 32 and 30 bits
        "assign o = 'b" + "0" * 31 + "1" + " + 'b_" + "1_" * 31 + ";",  # 32 binary digits
        "assign o = 'd000000000000005 + 'D0000000000000000007;",  # a decimal: every tool agrees
        "assign o = 36'h0_0000_000F + 33'b0" + "0" * 31 + "1 + 40'o00000000001;",  # sized
    ],
)
def test_an_unsized_number_of_32_bits_of_digits_or_a_sized_or_decimal_one_is_fine(
    text: str,
) -> None:
    ok(f"module m;\n{text}\nendmodule\n")


@pytest.mark.parametrize(
    ("text", "call"),
    [
        ("localparam C = $countones(UW);", "$countones"),
        ("assign o = ($countones(a) > -1) ? a : ~a;", "$countones"),
        ("localparam C = $countbits(UW, 1'b1);", "$countbits"),
        ("generate if ($countbits (UW, 1'b0) > -1) begin : g1 end endgenerate", "$countbits"),
    ],
)
def test_countones_and_countbits_are_refused(text: str, call: str) -> None:
    """Round 24 (s02, s06, s08, cntbits): a signed int in Icarus and Verilator, unsigned in Yosys
    0.62, so `$countones(x) > -1` is true in a simulator and false in the gates."""
    found = refused(text, f"`{call}`, whose result Yosys 0.62 reads as unsigned")
    message = sv.plain_message(found)
    assert "Count with a `for` loop in a `function automatic integer` instead" in message
    assert "Remove it: it reads a file" not in message


def test_a_name_that_holds_countones_is_fine() -> None:
    ok("module m;\nwire my$countones;\nassign o = a$countbits + b;\nendmodule\n")
    ok('module m;\ninitial $display("$countones");\nwire \\$countones ;\nendmodule\n')


@pytest.mark.parametrize(
    "text",
    [
        "assign o = 2147483647 + 32'd2147483648 + 32'sd4294967295 + 33'h1FFFFFFFF;",
        "assign o = 32 'd 3000000000 ^ 'h7FFFFFFF ^ 'd2147483647;",
        "localparam real R = 3000000000.0, E = 2e10, F = 1.5e+10;",
        "wire w2147483648; wire [31:0] v = 8'd255;",
        'initial $display("4294967296"); // 4294967296',
    ],
)
def test_sized_small_and_real_numbers_are_fine(text: str) -> None:
    ok(f"module m;\n{text}\nendmodule\n")


@pytest.mark.parametrize(
    ("header", "word"),
    [
        ("function automatic [7:0] f(input [7:0] a, output [7:0] o);", "output"),
        ("function automatic void f(input [7:0] a, output [3:0] h, output [3:0] l);", "output"),
        ("function automatic void f(inout [7:0] v);", "inout"),
        ("function automatic void f(ref logic [7:0] v);", "ref"),
        ("function automatic void f(const ref logic [7:0] v);", "ref"),
        ("function automatic [7:0] f;\n input [7:0] a;\n output [7:0] o;", "output"),
    ],
)
def test_a_function_argument_that_is_not_an_input_is_refused(header: str, word: str) -> None:
    """Round 13 w01ifctl, inoutif: Icarus cannot compile such a function, and the order check
    cannot follow a value a call writes back (Verilator turns `if (c) y = f(a, t);` into a
    ternary, and an inout actual is read before the call)."""
    found = refused(f"{header}\n f = a;\nendfunction", f"function with an `{word}` argument")
    assert "Return the value instead" in sv.plain_message(found)
    ok("module m (input [7:0] a, output [7:0] o, inout [7:0] io);\n"
       "function automatic [7:0] f(input [7:0] v); f = ~v; endfunction\n"
       "assign o = f(a);\nendmodule\n")  # fmt: skip


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
    # a word that starts with `-` is no code (LO10, round 22): with nothing after it, no code
    found = refused("// verilator lint_off -rule\nwire a;", "lint_off")
    assert found.what == "`verilator lint_off`", found
    found = refused("/* verilator lint_off -rule */\nwire a;", "lint_off")
    assert found.what == "`verilator lint_off`", found


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


@pytest.mark.parametrize(
    ("comment", "code"),
    [
        # round 19 lo01/lo03: Verilator 5.046 turns off every code of a comma list, so a race
        # warning after the first code was hidden while the scan looked at the first word only
        ("/* verilator lint_off WIDTH,BLKSEQ */", "BLKSEQ"),
        ("// verilator lint_off UNUSEDSIGNAL,BLKSEQ", "BLKSEQ"),
        ("// verilator lint_off UNUSED,LATCH", "LATCH"),
        ("// verilator lint_off WIDTH,,CASEINCOMPLETE", "CASEINCOMPLETE"),  # an empty item
        ("// verilator lint_off WIDTH,UNUSED,MULTIDRIVEN", "MULTIDRIVEN"),
        ("// verilator lint_off BLKSEQ,WIDTH", "BLKSEQ"),
    ],
)
def test_every_code_of_a_lint_off_list_is_judged(comment: str, code: str) -> None:
    found = refused(f"  {comment}\n  wire a;", "lint_off")
    assert found.line == 2 and found.what == f"`verilator lint_off {code}`", found


@pytest.mark.parametrize(
    ("comment", "code"),
    [
        ("// verilator lint_off blkseq", "blkseq"),
        ("/* verilator lint_off Latch */", "Latch"),
        ("// verilator lint_off WIDTH,latch", "latch"),
        ("// verilator lint_off width,CaseIncomplete", "CaseIncomplete"),
    ],
)
def test_a_lint_off_code_in_lower_or_mixed_case_is_judged_too(comment: str, code: str) -> None:
    """LO3 (round 20): Verilator 5.046 compares the codes without case (`lint_off blkseq` hides
    BLKSEQ), so the code is judged in upper case."""
    found = refused(f"  {comment}\n  wire a;", "lint_off")
    assert found.line == 2 and found.what == f"`verilator lint_off {code}`", found


def test_a_lint_off_list_of_other_codes_may_be_switched_off() -> None:
    ok("module m;\n  // verilator lint_off WIDTH,UNUSEDSIGNAL\n  wire a;\nendmodule\n")
    ok("module m;\n  /* verilator lint_off WIDTHTRUNC,WIDTHEXPAND */\n  wire a;\nendmodule\n")


def test_the_words_of_a_comment_after_a_lint_off_are_not_codes() -> None:
    """Round 20: Verilator 5.046 reads no code in a `//` comment after the list (`lint_off
    WIDTH // no latch here` still reports LATCH), so its words are not judged; the codes before
    it still are."""
    ok("module m;\n  // verilator lint_off WIDTH // cut sum, no latch here\n  wire a;\nendmodule\n")
    ok("module m;\n  /* verilator lint_off WIDTH */ // blkseq is not hidden\nendmodule\n")
    found = refused("  // verilator lint_off WIDTH,BLKSEQ // keep the race\n  wire a;", "lint_off")
    assert found.what == "`verilator lint_off BLKSEQ`", found
    found = refused("  // verilator lint_off BLKSEQ // a note about latch\n  wire a;", "lint_off")
    assert found.what == "`verilator lint_off BLKSEQ`", found


def test_lint_on_and_other_comments_are_fine() -> None:
    ok("// verilator lint_on BLKSEQ\n// verilator public\nmodule m; endmodule\n")


def test_the_reviewers_race_designs_need_the_lint_warning_to_stay() -> None:
    race = "reg [7:0] a, b;\nalways @(posedge clk) a = ui_in;\nalways @(posedge clk) b = a;\n"
    ok(f"module m;\n{race}endmodule\n")  # the race itself is the lint step's to catch
    refused("// verilator lint_off BLKSEQ\n" + race, "BLKSEQ")  # cases4/race, cases7/racen


# Round 21 (l01-l03, lo4-lo82, fixer probes p002-p133): every comment below hides BLKSEQ from
# Verilator 5.046's lint (`--lint-only` with the emit flags, a clocked block with blocking
# assignments after it). Its preprocessor deletes every `//`, `/*` and `*/` inside a comment
# before it reads a meta-comment (V3PreProc commentCleanup), reads `verilator` and `lint_off`
# with no blank between them too, and skips the rest of the keyword (`lint_offx BLKSEQ`); only a
# `//` comment that starts with exactly `verilator lint_off` ends at its first `/` (V3PreLex).
@pytest.mark.parametrize(
    "comment",
    [
        "/* verilator lint_off WIDTH,//,BLKSEQ */",  # l01
        "/* verilator lint_off WIDTH//,BLKSEQ */",  # l02, lo4
        "/* verilator lint_off B//LKSEQ */",  # l03
        "/* verilator lint_off WIDTH,//BLKSEQ */",
        "/*verilator lint_off WIDTH//,BLKSEQ*/",
        "/* verilator lint_off BLK//SEQ */",
        "/* verilator lint_off BLK////SEQ */",
        "/* verilator lint_off BLK///*SEQ */",
        "/* verilator lint_off WIDTH,/*BLKSEQ */",
        "/* verilator lint_off WIDTH,B/*LKSEQ */",
        "/* verilator /*lint_off BLKSEQ */",
        "/*verilator lint_//off BLKSEQ*/",
        "/* verilator lint//_off BLKSEQ */",
        "/* verilator//lint_off BLKSEQ */",
        "/*verilatorlint_off BLKSEQ*/",
        "/* verilator lint_offx BLKSEQ */",
        "// verilator /*lint_off BLKSEQ",
        "// verilator lint_/*off BLKSEQ",
        "// verilator lint_*/off BLKSEQ",
        "// verilator */lint_off BLKSEQ",
        "//  verilator  lint_off WIDTH,//BLKSEQ",  # not the exact spelling: no cut at `/`
        "// verilator\tlint_off WIDTH,//BLKSEQ",
        "// Verilator lint_off WIDTH,//BLKSEQ",
        "// verilatorlint_off BLKSEQ",
        "// verilator lint_offx BLKSEQ",
        "// verilator lint_off_x BLKSEQ",
        "// verilator lint_off WIDTH,// verilator lint_off BLKSEQ",  # a second comment
        "// verilator lint_on // verilator lint_off BLKSEQ",
        "// verilator lint_off WIDTH /* verilator lint_off BLKSEQ */",
    ],
)
def test_a_lint_off_is_read_as_verilator_reads_the_comment(comment: str) -> None:
    found = refused(f"  {comment}\n  wire a;", "lint_off")
    assert found.line == 2 and found.what == "`verilator lint_off BLKSEQ`", found


@pytest.mark.parametrize(
    "comment",
    [
        "// verilator lint_off WIDTH // no latch here",
        "// verilator lint_off WIDTH//no latch here",  # round 21 LS5: no blank before the `//`
        "/* verilator lint_off WIDTH */ // no latch here",
        "// verilator lint_off WIDTH,//BLKSEQ",  # Verilator ends this comment at the `/`
        "// verilator lint_off WIDTH, // BLKSEQ",
        "//\tverilator lint_off WIDTH,//BLKSEQ",  # a tab before `verilator` is still the rule
        '// verilator lint_off WIDTH "// BLKSEQ',
        "// verilator lint_off WIDTH /* no blkseq here */",  # closes on the line: a comment
        "// verilator lint_off WIDTH /* a */ // and no latch",
        "/* verilator lint_off WIDTH */ // verilator lint_off WIDTH // and BLKSEQ",
        # VC14 (round 23): a form feed after the block comment is a blank to Verilator, which
        # then reads the `//` comment to the end of the line
        "// verilator lint_off WIDTH /* a */\f// b",
    ],
)
def test_words_verilator_does_not_read_as_codes_are_not_judged(comment: str) -> None:
    """Verilator still reports BLKSEQ (or LATCH) after each of these comments."""
    ok(f"module m;\n  {comment}\n  wire a;\nendmodule\n")


# Round 22 (x20note, x21hdr; fixtures lonote, lohdr): Verilator 5.046 reads a comment as a
# meta-comment only when its text starts with `verilator` after blanks (V3PreProc::comment), so
# each note below still reports BLKSEQ or LATCH there; its words are no codes.
@pytest.mark.parametrize(
    "comment",
    [
        "/* No verilator lint_off is needed in this file: next_count is written\n"
        "     at the top of the always block, so Verilator reports no latch. */",
        "/*\n   * Lint policy: no verilator lint_off comments; there is no blocking race (BLKSEQ)\n"
        "   * and no latch.\n   */",
        "/* No verilator lint_off is needed: no\n     BLKSEQ here, and no latch. */",  # pr1
        "/* note verilator lint_off BLKSEQ */",  # pr2
        "/* //verilator lint_off BLKSEQ */",  # pq1: the `//` is not deleted before the test
        "/* /*verilator lint_off BLKSEQ */",  # pq2
        "/** verilator lint_off BLKSEQ */",
        "/*/ verilator lint_off BLKSEQ */",
        "// no verilator lint_off needed: the latch is gone",
        "// x // verilator lint_off BLKSEQ",  # one comment to the end of the line
    ],
)
def test_a_comment_that_does_not_start_with_verilator_has_no_codes(comment: str) -> None:
    ok(f"module m;\n  {comment}\n  wire a;\nendmodule\n")


@pytest.mark.parametrize(
    "comment",
    [
        "/*\n   verilator lint_off BLKSEQ */",  # pq3
        "/*\tverilator lint_off BLKSEQ */",  # ps7
        "/*\x0bverilator lint_off BLKSEQ */",
        "/*\x0cverilator lint_off BLKSEQ */",
        "/*\r\n   verilator lint_off BLKSEQ */",  # (a lone CR is refused on its own)
        "/* Verilator lint_off BLKSEQ */",  # pr4
        "/* VERILATOR lint_off BLKSEQ */",  # no meta-comment for Verilator: fails closed
        "// Verilator lint_off BLKSEQ",
    ],
)
def test_a_comment_that_starts_with_verilator_after_blanks_is_judged(comment: str) -> None:
    found = refused(f"  {comment}\n  wire a;", "lint_off")
    assert found.line == 2 and found.what == "`verilator lint_off BLKSEQ`", found


def test_a_lint_off_in_a_string_or_an_escaped_name_is_no_comment() -> None:
    """Verilator's preprocessor reads a string and an escaped identifier as one token (`\\x//v`
    is a name to it, as to Icarus and Yosys)."""
    ok('module m;\n  initial $display("/* verilator lint_off BLKSEQ */");\nendmodule\n')
    ok("module m;\n  wire \\x//verilator ;\n  // verilator lint_off WIDTH\nendmodule\n")
    text = 'x = "// a"; \\y/*b /* c */ // d\n'
    comments = list(sv._verilator_comments(text))
    assert comments == [(text.index("/* c"), "/* c */"), (text.index("// d"), "// d")]


def test_a_double_quote_is_a_blank_in_the_cleaned_reading() -> None:
    """LO6 (round 23): Verilator's comment cleanup makes a double quote a space, so
    `verilator"lint_off` reads as `verilator lint_off` there (Verilator then stops with
    BADVLTPRAGMA, so this refusal changes no verdict; it pins the cleaned reading)."""
    found = refused('  /* verilator"lint_off BLKSEQ */\n  wire a;', "lint_off")
    assert found.what == "`verilator lint_off BLKSEQ`", found


def test_the_words_are_judged_as_written_too() -> None:
    """Fail closed: a code counts before Verilator's cleanup joins it with the next word as well
    (`BLKSEQ//x` is `BLKSEQx` after it; Verilator stops with an error on that one)."""
    found = refused("  /* verilator lint_off BLKSEQ//x */\n  wire a;", "lint_off")
    assert found.what == "`verilator lint_off BLKSEQ`", found


def test_a_lint_off_over_several_lines_is_named_at_its_first_line() -> None:
    found = refused("  /* verilator\n     lint_off BLKSEQ */\n  wire a;", "lint_off")
    assert found.line == 2 and found.what == "`verilator lint_off BLKSEQ`", found


@pytest.mark.parametrize(
    "text",
    [
        # Verilator ends the comment at `/*` and skips to the `*/` on the last line, so it lints
        # the nonblocking copy there, and the other tools read the blocking race (fixture locut)
        "  // verilator lint_off WIDTH /*\n  always @(posedge clk) begin a = i; b = a; end\n"
        "  // */ always @(posedge clk) begin a <= i; b <= a; end",
        "  // verilator lint_on /*\n  wire a;\n  // */",
        # Verilator reads `else q = 8'd0;` as code: no latch for its lint, a latch for Yosys
        "  always @* if (en) q = d; // verilator lint_off WIDTH /**/ else q = 8'd0;",
        "  // verilator lint_off WIDTH / 2",
        '  // verilator lint_off WIDTH "/x',
        "  // verilator lint_off WIDTH /* never closed on this line\n  wire a; */",
        # VC1 (round 22): Verilator ends a `// verilator lint_save` or `lint_restore` comment
        # at its first `/` too (a syntax error at the `/`, and a block comment over the lines)
        "  // verilator lint_save / 2",
        "  // verilator lint_restore / 2",
        "  // verilator lint_save /* x\n  wire a; */",
        "  // verilator lint_restore /* x\n  wire a; */",
        # VC16 (round 23): a form feed between `//` and `verilator` is a blank to Verilator's
        # preprocessor, which still ends the comment at the `/`
        "  //\fverilator lint_off WIDTH / x",
        "  //\f verilator lint_off WIDTH /* x\n  wire a; */",
    ],
)
def test_a_lint_off_comment_that_goes_on_after_a_single_slash_is_refused(text: str) -> None:
    found = refused(text, "goes on after a single `/`")
    assert found.line == 2, found
    assert "`// verilator lint_off WIDTH // why`" in sv.plain_message(found)


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


def test_myhdl_style_event_lists_are_not_judged_by_the_strict_rule() -> None:
    text = "module m ();\nalways @(a, b, c) begin: l\n  y = a & b;\nend\nendmodule\n"
    assert sv.refusal([("core.v", text)], ()) is None
    assert sv.refusal([("core.v", text)], {"core.v"}) is not None
    assert sv.refusal([("core.v", text)], (), {"core.v"}) is None  # it lists what it reads


def test_a_plain_function_is_refused_and_an_automatic_one_is_fine() -> None:
    refused("function [7:0] f;\n input [7:0] a;\n f = a;\n endfunction", "automatic")
    ok("module m; function automatic [7:0] f; input [7:0] a; f = a; endfunction endmodule\n")


@pytest.mark.parametrize("form", ["task", "task automatic"])
def test_every_task_is_refused(form: str) -> None:
    found = refused(f"{form} wr;\n t = cnt;\n endtask", "a `task`")
    assert "function automatic" in sv.plain_message(found)


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
        "// Header comment\n// Generated by MyHDL 0.11.51\n"
        "`timescale 1ns/10ps\n\nmodule counter (\n  input clk,\n  input rst_n,\n"
        "  output reg [7:0] uo_out\n);\n"
        "// verilator lint_off UNUSEDSIGNAL\nreg [7:0] count;\n\n"
        "always @(posedge clk, negedge rst_n) begin: COUNTER_SEQ\n"
        "  if (rst_n == 0) begin\n    count <= 0;\n  end else begin\n    count <= (count + 1);\n"
        "  end\nend\n\n"
        "always @(count, rst_n) begin: COUNTER_COMB\n  uo_out = count;\nend\n\nendmodule\n"
    )
    assert sv.refusal([("counter.v", myhdl)], (), {"counter.v"}) is None


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
        "function automatic void f(output o); o = 1; endfunction",
        "assign y = 2147483648;",
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


# -- plain ASCII outside comments: the same lexer boundaries as Yosys, Icarus, Verilator --------


ODD_CHARS = {
    "NBSP": "\u00a0",
    "NEL": "\u0085",
    "LINE SEPARATOR": "\u2028",
    "PARAGRAPH SEPARATOR": "\u2029",
    "VT": "\x0b",
    "FF": "\x0c",
    "FS": "\x1c",
    "DEL": "\x7f",
    "NUL": "\x00",
    "e acute": "\u00e9",
    "U+FFFD": "\ufffd",
}


@pytest.mark.parametrize("char", list(ODD_CHARS.values()), ids=list(ODD_CHARS))
def test_a_character_outside_plain_ascii_is_refused_outside_comments(char: str) -> None:
    for text in (
        f"module m;\n  wire a{char}b;\nendmodule\n",  # in code
        f'module m;\n  initial $display("a{char}b");\nendmodule\n',  # in a string
        f"module m;\n  wire \\id{char}x ;\nendmodule\n",  # in an escaped identifier
        f"module m;\n  (* keep = 1{char} *) wire a;\nendmodule\n",  # in an attribute
    ):
        found = sv.refusal([("d.v", text)])
        assert found is not None and "outside plain ASCII" in found.what, text
        assert found.line == 2 or found.line == 1


@pytest.mark.parametrize("char", list(ODD_CHARS.values()), ids=list(ODD_CHARS))
def test_the_same_characters_are_fine_inside_comments(char: str) -> None:
    ok(f"module m; // a{char}b \u2265 1 \u00b5m\n  /* x{char}y\n  z{char} */ wire a;\nendmodule\n")
    text = f"wire a; // a{char}b\nwire b; /* {char} */\n"
    clean = sv.clean(text)
    assert clean.startswith("wire a;") and "wire b;" in clean
    assert char not in clean and len(clean) == len(text)


@pytest.mark.parametrize("char", ["\u00a0", "\u0085", "\u2028"])
def test_the_reviewers_escaped_identifier_tricks_are_refused(char: str) -> None:
    """cases10/nbspoff2, lsoff, nbspdel: after `\\h<char>` the tools still read the identifier, so
    the `/*` that follows starts no comment for them (it does for a lexer that ends at the char)."""
    hidden_off = (
        f"module m;\n  reg \\h{char}/* , \\e*/ ;\n  always @* begin\n"
        f"    \\h{char}/* = 1'b0; \\e*/ = 1'b0;\n"
        "    // synopsys translate_off\n    o = ~ui_in;\n    // synopsys translate_on\n"
        "  end\nendmodule\n"
    )
    hidden_delay = (
        f"module m;\n  always @(posedge clk) begin\n"
        f"    \\h{char}/* <= 1'b0; #7; \\e*/ <= 1'b0;\n  end\nendmodule\n"
    )
    for text in (hidden_off, hidden_delay):
        found = sv.refusal([("d.v", text)])
        assert found is not None and "outside plain ASCII" in found.what


@pytest.mark.parametrize(
    ("name", "char", "what"),
    [
        # es02 (round 20): Icarus expands the predefined macro inside the name and reads `\v1`,
        # which inside block g is g's own `\v1`; Yosys and Verilator read the module's wire
        ("\\v`__ICARUS__", "`", "a back-tick inside an escaped identifier"),
        ("\\v`undefined_macro", "`", "a back-tick inside an escaped identifier"),  # es01
        ("\\`a", "`", "a back-tick inside an escaped identifier"),
        # pd03, rk18: Verilator spells the name `M\"`, Icarus `M"`: the values of the two tools
        # were never compared (and a real localparam so named counted as dead code)
        ('\\M"', '"', "a double quote inside an escaped identifier"),
        ('\\a"b', '"', "a double quote inside an escaped identifier"),
    ],
)
def test_a_back_tick_or_double_quote_inside_an_escaped_identifier_is_refused(
    name: str, char: str, what: str
) -> None:
    body = f"  wire [7:0] x;\n  localparam {name} = 1;\n  assign x = {name} ;\n"
    text = f"module m;\n{body}endmodule\n"
    problem = sv.lex(text)[2]
    assert problem is not None and problem[0] == text.index(char) and what in problem[1]
    found = sv.refusal([("d.v", text)])
    assert found is not None and found.line == 3 and what in found.what, found
    message = sv.plain_message(found)
    assert "Rename it to a plain name" in message and "Icarus expands a macro" in message


@pytest.mark.parametrize(
    "name", ["\\v1", "\\a$b", "\\u_sub.x", "\\fsm[0]", "\\a/*b", "\\a'b", "\\x+y", "\\#5"]
)
def test_other_escaped_identifiers_are_fine(name: str) -> None:
    ok(f"module m;\n  wire [7:0] x;\n  wire [7:0] {name} ;\n  assign x = {name} ;\nendmodule\n")


def test_an_escaped_identifier_ends_at_space_tab_cr_or_lf_only() -> None:
    # the `/*` right after `\\a` is part of the identifier, the one after a blank starts a comment
    assert sv.clean("\\a/* b; /* c */ d;\n") == "\\a/* b;         d;\n"
    for blank in (" ", "\t", "\r", "\n"):
        assert sv.clean(f"\\a{blank}/* c */ d;\n") == f"\\a{blank}        d;\n"


# -- z and ? only as the label of a casez item ---------------------------------------


def casez(body: str, header: str = "casez (ui_in[7:6])") -> str:
    head = "module m;\nreg [7:0] y;\nalways @* begin\n  y = 8'd0;\n"
    return f"{head}  {header}\n{body}\n  endcase\nend\nendmodule\n"


@pytest.mark.parametrize(
    "items",
    [
        "    2'b0?: y = 1;\n    2'b1?: y = 2;",
        "    2'b0?, 2'b1?: y = 1;\n    default: y = 0;",
        "    2'b?1,\n    2'bz0 : y = 1;",
        "    2'b1?: begin y = 1; end\n    2'b?1: y = 2;",
        "    2'b1?: y = 1;\n    2'b0?: if (ui_in[0]) y = 2; else y = 3;\n    2'bzz: y = 4;",
        "    2'b1?: case (ui_in[0]) 1'b0: y = 1; default: y = 2; endcase\n    2'b0?: y = 3;",
        "    'z: y = 1;",
    ],
)
def test_wildcards_are_accepted_as_casez_item_labels(items: str) -> None:
    ok(casez(items))


def test_a_casez_inside_a_plain_case_still_takes_wildcards() -> None:
    ok(
        "module m; always @* case (a)\n  1'b0: y = 0;\n  1'b1: casez (b)\n    2'b1?: y = 1;\n"
        "    default: y = 2;\n  endcase\nendcase endmodule\n"
    )


@pytest.mark.parametrize(
    ("name", "items", "header"),
    [
        ("zcasezC", "    1'b?: if (ui_in[0] == 1'bz) y = 8'd1;", "casez (ui_in[7])"),
        ("zcasezA", "    1'b?: if (ui_in[1:0] == 2'bzz) y = 8'd1;", "casez (ui_in[7])"),
        ("zcasezB", "    1'b?: if ((ui_in[1:0] & 2'bz1) == 2'b01) y = 8'd1;", "casez (ui_in[7])"),
        ("zcasezD", "    1'b?: if (ui_in[1:0] == 2'b??) y = 8'd1;", "casez (ui_in[7])"),
        (
            "nestz",
            "    1'b?: case (ui_in[1:0])\n 2'b1z: y = 8'd1;\n default: y = 8'd0;\n endcase",
            "casez (ui_in[7])",
        ),
        (
            "nestq",
            "    1'b?: case (ui_in[1:0])\n 2'b1?: y = 8'd1;\n default: y = 8'd0;\n endcase",
            "casez (ui_in[7])",
        ),
        ("in the header", "    1'b0: y = 1;", "casez (ui_in[1:0] == 2'b1?)"),
        ("in a concatenation", "    1'b0: y = {1'b1, 1'bz};", "casez (ui_in[7])"),
        ("in a ternary", "    1'b0: y = ui_in[0] ? 8'bz : 8'd1;", "casez (ui_in[7])"),
        ("in a statement", "    1'b0: y = 8'bzzzz_0000;", "casez (ui_in[7])"),
        ("in a bit select", "    1'b0: y[1'bz] = 1;", "casez (ui_in[7])"),
        ("in a parenthesis", "    (1'b?): y = 1;", "casez (ui_in[7])"),
        ("in a delay-free expression", "    1'b0: y = (a == 2'b1?) ? 1 : 2;", "casez (ui_in[7])"),
        ("after an operator", "    1'b0: y = 1; | 1'b?: y = 2;", "casez (ui_in[7])"),
        ("a label with no colon", "    1'b?;", "casez (ui_in[7])"),
    ],
)
def test_a_wildcard_anywhere_else_is_refused(name: str, items: str, header: str) -> None:
    found = sv.refusal([("d.v", casez(items, header))])
    assert found is not None, name
    assert "z or ?" in found.what or "x or z" in found.what, (name, found)


def test_a_plain_case_never_takes_wildcards() -> None:
    refused("always @* case (a)\n 2'b1?: y = 1;\n default: y = 0;\nendcase", "z or ?")


def test_wildcard_equality_and_inside_are_refused() -> None:
    refused("always @* if (a ==? b) y = 1; else y = 2;", "==?")  # cases9/wildeq
    refused("always @* if (a !=? b) y = 1; else y = 2;", "!=?")
    refused("always @* if (a inside {2'd1, 2'd2}) y = 1; else y = 2;", "inside")  # cases9/insideop


# -- MyHDL: a level-sensitive block must be the plain shape @always_comb writes ----------------

MYHDL_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "equiv_myhdl"


def _core(path: Path) -> sv.Refusal | None:
    return sv.refusal([(path.name, path.read_text(encoding="latin-1"))], (), {path.name})


@pytest.mark.parametrize("path", sorted((MYHDL_FIXTURES / "ok").glob("*.v")), ids=lambda p: p.stem)
def test_what_the_myhdl_front_end_writes_for_always_comb_is_accepted(path: Path) -> None:
    """Emitted by MyHDL 0.11: counters, state machines (case, enums), local variables, slices,
    concatenations, a list of signals (`mem[0], mem[1], ...`), a for loop, a ROM `case`."""
    assert _core(path) is None


# what each reviewer core must be refused for (a part of the plain message)
BAD = {
    "my41": "non-blocking", "myalias": "non-blocking", "mycall": "non-blocking",
    "mysens2": "non-blocking", "mysub": "non-blocking", "myvar": "non-blocking",
    "myfst": "not `automatic`",  # a static function: the rule is for every front-end
    "mylab1": "reads `cnt` without listing it",  # the label is not a signal
    "mylab2": "not a statement", "mylab3": "named block", "mylab4": "not a statement",
    "mydecl": "not a statement",
    "myarr": "not a word of an array", "myarr2": "not a word of an array",
    "mybits": "not a word of an array",  # a bit item `cnt[0]` for a plain vector
    "myesc": "escaped identifier",
    "mycase1": "case item that is not a number", "mycase2": "case item that is not a number",
    "myglob": "`global` is not a plain name",  # declared as a reg: a keyword as a name
    "myconcat": "not a statement MyHDL writes",
    "myfor": "reads `cnt` without listing it",
    "myrep": "`repeat`", "mywhile": "`while`",
    "mylocal": "reads its local `held` before it is written",
    # round 8: the module around the level blocks is parsed too
    "aneg0": "the array `mem` has no word", "aneg1": "the array `mem` has no word",
    "amulti": "`helper` at module level", "amulti2": "`helper` at module level",
    "agenif": "`generate` at module level", "agensh": "`generate` at module level",
    # and a signal is not read before the block writes it (genuine MyHDL: mrbw2)
    "brbw": "reads `t` before it writes it", "mrbw2": "reads `t` before it writes it",
    # round 9: a loop reads what a later iteration writes (gloopit is genuine MyHDL); no `@*`
    "gloopit": "both reads and writes `t` in a for loop", "mloopit": "in a for loop",
    "mstarloop": "explicit lists only",
}  # fmt: skip


@pytest.mark.parametrize("path", sorted((MYHDL_FIXTURES / "bad").glob("*.v")), ids=lambda p: p.stem)
def test_the_reviewers_cores_are_refused_with_a_clear_message(path: Path) -> None:
    """mysens2, the five mcp8 dodges (they all emit `always @(b0) begin: label y <= cnt; end`) and
    the mcp10 cores that a `convert()` of the agent's own writes by hand."""
    found = _core(path)
    assert found is not None and found.code == "sim_synth", path.name
    assert BAD[path.stem] in found.what, found.what
    if "level-sensitive" in found.what:
        assert "@always_comb" in sv.plain_message(found)


def test_every_bad_core_has_an_expectation() -> None:
    assert {p.stem for p in (MYHDL_FIXTURES / "bad").glob("*.v")} == set(BAD)


def _my(block: str, extra: str = "") -> sv.Refusal | None:
    text = f"module m ();\n{extra}\n{block}\nendmodule\n"
    return sv.refusal([("m.v", text)], (), {"m.v"})


def _lvl(body: str, items: str = "a", extra: str = "") -> sv.Refusal | None:
    return _my(f"always @({items}) begin: l\n{body}\nend", extra)


ARR = "reg [7:0] m [0:4-1];"


@pytest.mark.parametrize(
    ("body", "items", "extra"),
    [
        ("y = a & b;", "a, b", ""),
        ("y = a & b;", "a or b", ""),
        ("reg [3:0] t;\n t = a;\n y = t + b;", "a, b", ""),
        ("reg signed [3:0] t;\n integer k;\n t = a;\n k = t;\n y = k;", "a", ""),
        (
            "case (sel)\n 2'b00: y = a;\n 2'b01, 2'b10: begin\n y = b;\n end\n default: y = 0;\n"
            " endcase",
            "sel, a, b",
            "",
        ),
        ("casez (sel)\n 2'b0?: y = a;\n default: y = 0;\n endcase", "sel, a", ""),
        ("y[3:0] = a[2:0];\n z[0] = a;", "a", ""),
        ("y[i] = a;", "a, i", ""),
        ("y[3:i] = a;", "a, i", ""),
        ("y = $signed(a);", "a", ""),
        ("y = $unsigned(a) <= b;", "a, b", ""),
        ("y = a ? b : 8'h0f;", "a, b", ""),
        ("y = {a, b[1:0]};", "a, b", ""),
        ("if (a) y = 1;\n else if (b) y = 2;\n else y = 3;", "a, b", ""),
        ("if (a) begin\n y = 1;\n end\n else begin\n y = 2;\n end", "a", ""),
        ("y = m[s];", "m[0], m[1], m[2], m[3], s", ARR),
        ("y = m[2];", "m[2]", ARR),
        ("y = m[2] + m[0];", "m[2], m[0]", ARR),
        (
            "integer i;\n y = 0;\n for (i = 0; i < 8; i = i + 1) begin\n  if (a[i]) y = y + 1;\n"
            " end",
            "a, y",
            "",
        ),
        ("y = a;\n z = y;", "a, y", ""),  # written and read: listed
    ],
)
def test_a_level_block_of_the_plain_shape_that_lists_what_it_reads_is_accepted(
    body: str, items: str, extra: str
) -> None:
    assert _lvl(body, items, extra) is None, body


@pytest.mark.parametrize(
    "block",
    [
        "always @(posedge clk) begin\n  q <= d;\nend",
        "always @(posedge clk, negedge rst_n) begin: s\n  q <= d;\nend",
    ],
)
def test_edge_and_star_blocks_are_not_the_level_rule(block: str) -> None:
    assert _my(block) is None, block


@pytest.mark.parametrize(
    ("body", "items", "extra", "missing"),
    [
        ("y = b;", "a", "", "b"),
        ("y = a;\n z = y;", "a", "", "y"),  # written and read
        ("y[b] = a;", "a", "", "b"),  # a target's index is a read
        ("y = a ? b : c <= d;", "a, b", "", "c"),
        ("y = a;\n if (a) z = k;", "a", "", "k"),
        ("reg [3:0] t;\n t = a;\n y = t + u;", "a", "", "u"),
        ("y = m[s];", "m[0], m[1], m[2], m[3]", ARR, "s"),
        ("y = m[s];", "s", ARR, "m"),  # an array not listed word by word
        ("y = m[s];", "m[0], s", ARR, "m"),  # m[1..3] missing
        ("y = m[1] + m[2];", "m[0], m[1]", ARR, "m"),
        ("y = m;", "m[0], m[1], m[2], m[3]", "reg [7:0] m [0:5-1];", "m"),  # a fifth word
        ("y = a;\n case (b)\n 0: z = a;\n endcase", "a", "", "b"),
        ("y = a;\n for (j = 0; j < n; j = j + 1) z = a;", "a, j", "", "n"),
        ("y = a[idx];", "a", "", "idx"),
    ],
)
def test_a_level_block_that_leaves_out_a_signal_it_reads_is_refused(
    body: str, items: str, extra: str, missing: str
) -> None:
    found = _lvl(body, items, extra)
    assert found is not None, body
    assert f"`{missing}` without listing it" in found.what, found.what
    assert found.label == "l"


def test_a_plain_vector_must_be_listed_by_name_a_bit_is_not_enough() -> None:
    found = _lvl("y = a[3];", "a[3]")
    assert found is not None and "not a word of an array" in found.what
    found = _lvl("y = a[3];", "a[3]", "reg [7:0] a;")
    assert found is not None and "not a word of an array" in found.what


# every refusal of the grammar: what it is, the block body, the list, the module-level text
GRAMMAR = [
    ("a non-blocking assignment", "y <= a;", "a", ""),
    ("a non-blocking assignment", "y[1] <= a;", "a", ""),
    ("not a statement MyHDL writes", "begin: inner\n y = a;\n end", "a", ""),
    ("named block", "if (a) begin: inner\n y = a;\n end", "a", ""),
    ("a statement MyHDL writes", "begin\n y = a;\n end", "a", ""),  # a bare block
    ("a statement MyHDL writes", "disable l;", "a", ""),
    ("a statement MyHDL writes", "fork\n y = a;\n join", "a", ""),
    ("`while`", "while (a) y = 1;", "a", ""),
    ("`repeat`", "repeat (3) y = a;", "a", ""),
    ("`forever`", "forever y = a;", "a", ""),
    ("a delay", "#1 y = a;", "a", ""),
    ("a statement MyHDL writes", "@(a) y = a;", "a", ""),
    ("a statement MyHDL writes", "@(posedge clk) y = a;", "a", ""),
    ("a call `f(...)`", "y = f(a);", "a", ""),
    ("a call `f(...)`", "f(a);", "a", ""),
    ("a call `f(...)`", "y = a + f(1);", "a", ""),
    ("the system function `$display`", "y = $display(a);", "a", ""),
    ("the system function `$random`", "y = $random;", "a", ""),
    ("reserved word `global`", "y = global;", "a", ""),
    ("reserved word `logic`", "y = logic;", "a", ""),
    ("not a statement MyHDL writes", "global = a;", "a", ""),
    ("not a statement MyHDL writes", "logic = a;", "a", ""),
    ("a level list item", "y = a;", "global", ""),
    ("not a word of an array", "y = a;", "a[1]", ""),  # `a` is not an array
    ("not a word of an array", "y = m[0];", "m[4]", ARR),  # past the last word
    ("not a word of an array", "y = a;", "a[0]", "reg [7:0] a;"),  # a vector, not an array
    (
        "not a statement MyHDL writes",
        "reg [3:0] t;\n t = a;\n if (a) begin\n reg [3:0] u;\n end",
        "a",
        "",
    ),
    ("a case item that is not a number", "case (a)\n b: y = 1;\n endcase", "a", ""),
    ("a case item that is not a number", "case (a)\n y <= 8'd1: y = 1;\n endcase", "a", ""),
    ("a case item that is not a number", "case (1'b1)\n a: y = 1;\n endcase", "a", ""),
    ("not a statement MyHDL writes", "{y, z} = {a, a};", "a", ""),
    ("an expression that is empty", "y = ;", "a", ""),
    ("`;` in an expression", "y = (a;", "a", ""),
    ("an unmatched", "y = a);", "a", ""),
    ("`.` in an expression", "y = a.b;", "a", ""),
    ("`=` in an expression", "y = (a = 1);", "a", ""),
    ("`@` in an expression", "y = a @ 1;", "a", ""),
    ("reserved word `end`", "y = a", "a", ""),
    ("expected `=`", "y a;", "a", ""),
    ("a range that is not plain numbers", "reg [W:0] t;\n t = a;\n y = t;", "a", ""),
    ("declared twice", "reg t;\n reg t;\n t = a;\n y = t;", "a", ""),
    ("declared twice", "reg l;\n l = a;\n y = l;", "a", ""),
    (
        "a for loop that steps another variable",
        "integer i;\n for (i = 0; i < 2; j = j + 1) y = a;",
        "a, j",
        "",
    ),
    # a local is written before it is read, or it is static
    ("reads its local `t` before it is written", "reg [3:0] t;\n y = t;\n t = a;", "a", ""),
    ("reads its local `t` before it is written", "reg [3:0] t;\n t = t + a;\n y = t;", "a", ""),
    ("only in part or conditionally", "reg [3:0] t;\n if (a) t = 1;\n y = t;", "a", ""),
    ("only in part or conditionally", "reg [3:0] t;\n if (a) t = 1;\n t = 2;\n y = t;", "a", ""),
    ("only in part or conditionally", "reg [3:0] t;\n t[1] = a;\n y = t;", "a", ""),
    (
        "reads its local `i` before it is written",
        "integer i;\n y = a;\n for (i = 0; i < 2; i = i + 1) y = a;\n z = i;",
        "a",
        "",
    ),
    ("reads its local `i` before it is written", "integer i;\n y = i;", "a", ""),
    ("not a statement MyHDL writes", "reg t;\n y = a;\n reg u;", "a", ""),
]


@pytest.mark.parametrize(
    ("what", "body", "items", "extra"),
    GRAMMAR,
    ids=[f"{i}-{g[0][:24]}" for i, g in enumerate(GRAMMAR)],
)
def test_what_is_not_the_plain_shape_is_refused(
    what: str, body: str, items: str, extra: str
) -> None:
    found = _lvl(body, items, extra)
    assert found is not None, body
    assert what in found.what, (found.what, body)
    assert "@always_comb" in sv.plain_message(found) or "delay" in found.what


@pytest.mark.parametrize(
    "block",
    [
        "always @(a[3:0]) begin: l\n y = a;\nend",  # a part select is not a signal name
        "always @(a & b) begin: l\n y = a;\nend",
        "always @(a, posedge clk) begin: l\n y = a;\nend",  # edge and level mixed
        "always @(posedge clk, b) begin: l\n y = a;\nend",
        "initial @(a) begin: l\n y = a;\nend",  # not an always block
        "always @a begin: l\n y = a;\nend",
        "always @(a) y = a;",  # not `begin ... end`
        "always @(a) begin\n y = a;\nend",  # unlabelled
        "always @(a) begin: global\n y = a;\nend",  # a reserved word as the label
        "always @(a) begin: l\n y = a;\n",  # never closed
        "always @(a) begin: l\n case (a)\n 0: y = a;\nend",  # endcase missing
        "always @(a",  # not closed
        "always @() begin: l\n y = a;\nend",
    ],
)
def test_a_level_block_the_parser_cannot_vouch_for_is_refused(block: str) -> None:
    assert _my(block) is not None, block


def test_an_escaped_identifier_anywhere_in_a_myhdl_core_is_refused() -> None:
    found = _my("assign \\cnt = 1'b0;\nalways @(a) begin: l\n y = a;\nend")
    assert found is not None and "escaped identifier" in found.what


def test_a_function_or_task_needs_automatic_in_every_front_end() -> None:
    plain = "module m;\nfunction [7:0] f;\n input [7:0] a;\n f = a;\n endfunction\nendmodule\n"
    for hand, core in (({"m.v"}, ()), ((), {"m.v"}), ((), ())):
        found = sv.refusal([("m.v", plain)], hand, core)
        assert found is not None and "automatic" in found.what
    assert sv.refusal([("m.v", plain.replace("function [", "function automatic ["))], ()) is None


def test_the_label_of_the_refused_block_is_named() -> None:
    found = _lvl("y = b;")
    assert found is not None and found.label == "l" and found.line == 4


def test_the_strict_rule_still_refuses_every_level_list_in_verilog_and_amaranth() -> None:
    refused("always @(a, b) begin\n  y = a & b;\nend", "event control")


# -- a lone CR, and `*)` in a string inside an attribute ----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "// a comment\rassign y = 1'b0;\n",  # Icarus ends the comment at the CR
        "assign a = b;\r",
        "assign a = b;\rassign c = d;\n",
        "/* c\r */ assign a = b;\n",
        "assign a = b;\r\r\n",
    ],
)
def test_a_lone_cr_is_refused_anywhere(text: str) -> None:
    found = sv.refusal([("d.v", text)])
    assert found is not None and "carriage return" in found.what


def test_crlf_files_are_accepted() -> None:
    ok("// a comment\r\nassign a = b;\r\n/* c\r\n */\r\nassign c = d;\r\n")
    assert sv.lex("a;\r\nb;\r\n")[2] is None


@pytest.mark.parametrize(
    "text",
    [
        '(* note = "*) y = 1; //" *) reg s;',  # Verilator ends the attribute at the first `*)`
        '(* a = "x", b = "*)" *) reg s;',
        '(* a = "(*)" *) reg s;',
    ],
)
def test_an_attribute_with_a_close_inside_a_string_is_refused(text: str) -> None:
    refused(text, "inside a string")


def test_amaranths_src_strings_and_ordinary_attribute_strings_are_fine() -> None:
    ok('(* src = "top.py:12", keep *) reg s;\n(* note = "a (* b" *) reg t;\n')


# -- a signal is not read before the block writes it ----------------------------------------------


@pytest.mark.parametrize(
    ("body", "items"),
    [
        ("y = t;\n t = cnt;", "t, cnt"),  # the reviewer's mrbw2
        ("if (a) y = t;\n else y = 0;\n t = cnt;", "a, t, cnt"),
        ("y = y + 1;", "y"),  # a read, then a write of the same name
        ("if (a) begin\n z = y;\n end\n if (b) begin\n y = 1;\n end", "a, b, y"),  # a branch after
        ("z = y;\n y[3] = a;", "y, a"),  # a partial write later is a write too
        ("integer i;\n for (i = 0; i < t; i = i + 1) y = a;\n t = 3;", "a, t"),
        ("y = m[0];\n m[1] = a;", "a, m[0], m[1]"),
    ],
)
def test_a_signal_read_before_the_block_writes_it_is_refused(body: str, items: str) -> None:
    found = _lvl(body, items, ARR if "m[" in body else "")
    assert found is not None, body
    assert "before it writes it" in found.what and "write each signal" in found.what
    assert found.label == "l"


@pytest.mark.parametrize(
    ("body", "items"),
    [
        (
            "reg [7:0] v;\n sel = a;\n v = m[sel];\n y = v;",
            "a, sel, m[0], m[1], m[2], m[3]",
        ),  # lcomb.v
        ("t = cnt;\n y = t;", "cnt, t"),  # written, then read
        ("y = 0;\n if (a) y = y + 1;", "a, y"),  # written first, so the later read is fine
        ("y = a;\n z = y;\n y = b;", "a, b, y"),  # read after the first write
        ("if (a) y = 1;\n else y = 2;\n z = y;", "a, y"),  # writes, then a read
        ("integer i;\n y = 0;\n for (i = 0; i < 4; i = i + 1) y = y + a;", "a, y"),
        ("j = 0;\n for (k = j; k < 4; k = k + 1) y = k;\n z = k;", "j, k"),
    ],
)
def test_a_signal_written_before_it_is_read_is_accepted(body: str, items: str) -> None:
    extra = ARR if "m[" in body else ""
    assert _lvl(body, items, extra) is None, body


# -- the module around the level blocks: exactly one plain module ---------------------------------

MOD = """module m (clk, a, y);
input clk;
input [7:0] a;
output [7:0] y;
wire [7:0] y;
reg [7:0] r;
{items}
endmodule
"""


def _module(items: str) -> sv.Refusal | None:
    return sv.refusal([("m.v", MOD.format(items=items))], (), {"m.v"})


@pytest.mark.parametrize(
    "items",
    [
        "reg [7:0] mem [0:3];",
        "reg [7:0] mem [0:4-1];",
        "reg [7:0] mem [0:0];",  # one word
        "reg [7:0] mem [0:2-2];",
        "integer j;\nlocalparam P = 8'd5;\nparameter Q = 2;\nwire signed [3:0] w = 4'd1;",
        "wire [7:0] w;\nassign w = r ^ a;\nassign w[0] = r[1];",
        "initial begin\n  r = 0;\nend",
        "always @(posedge clk, negedge a) begin: s\n  r <= a;\nend",
        "always @(posedge clk or negedge a) begin: s\n  r <= a;\nend",
        "always @(a) begin: l\n  r = a;\nend",
    ],
)
def test_the_items_a_myhdl_core_may_have_are_accepted(items: str) -> None:
    assert _module(items) is None, items


def test_an_ansi_port_list_and_directives_are_read() -> None:
    text = (
        "`timescale 1ns/1ps\n`default_nettype none\nmodule m (\n  input wire clk,\n"
        "  input wire [7:0] a,\n  output reg [7:0] y\n);\nalways @(a) begin: l\n  y = a;\nend\n"
        "endmodule\n"
    )
    assert sv.refusal([("m.v", text)], (), {"m.v"}) is None


@pytest.mark.parametrize(
    ("what", "items"),
    [
        ("the array `mem` has no word", "reg [7:0] mem [0:2-3];"),  # aneg0
        ("the array `mem` has no word", "reg [7:0] mem [0:1-3];"),  # aneg1
        ("is not `[0:K]`", "reg [7:0] mem [3:0];"),
        ("is not `[0:K]`", "reg [7:0] mem [4];"),
        ("is not `[0:K]`", "reg [7:0] mem [1:3];"),
        ("with plain numbers", "reg [7:0] mem [0:K];"),
        ("with plain numbers", "reg [7:0] mem [0:3+1];"),
        ("second dimension", "reg [7:0] mem [0:1][0:1];"),
        ("an initializer", "reg [7:0] mem [0:1] = 0;"),
        ("not a plain `reg` declared once", "wire [7:0] mem [0:1];"),
        ("not a plain `reg` declared once", "reg [7:0] mem;\nreg [7:0] mem [0:1];"),  # awire
        ("is an array and is declared twice", "reg [7:0] mem [0:1];\nreg [7:0] mem;"),
        ("not a plain `reg` declared once", "reg [7:0] mem [0:1];\nreg [7:0] mem [0:2];"),
        (
            "`generate` at module level",
            "generate if (1) reg [7:0] mem [0:1]; endgenerate",
        ),  # agenif
        (
            "`generate` at module level",
            "generate if (1) begin: g\n reg [7:0] mem;\n end endgenerate",
        ),
        ("`genvar` at module level", "genvar g;"),
        ("`begin` at module level", "begin\n r = a;\nend"),
        ("`if` at module level", "if (a) r = 1;"),
        ("`for` at module level", "for (j = 0; j < 2; j = j + 1) r = a;"),
        ("`case` at module level", "case (a)\n 0: r = 1;\nendcase"),
        ("`helper` at module level", "helper u (.clk(clk));"),  # amulti: an instance
        ("`always_comb` at module level", "always_comb begin\n r = a;\nend"),
        (
            "`function` at module level",
            "function automatic [7:0] f;\n input a;\n f = a;\nendfunction",
        ),
        ("`specify` at module level", "specify\nendspecify"),
        ("`defparam` at module level", "defparam u.P = 1;"),
        ("`tri` at module level", "tri t;"),
        ("`module` at module level", "module n ();\nendmodule"),
        ("after the module", "endmodule\nmodule n ();\nendmodule"),  # amulti2: a second module
        ("`;` is not a plain name", "assign;"),
        ("a call `f(...)`", "assign y = f(a);"),
        ("a call `f(...)`", "wire [7:0] w = f(a);"),
        ("where `begin` was expected", "initial r = 0;"),
        ("where `begin` was expected", "always @(posedge clk) r <= a;"),
        ("`@` inside a block", "initial begin\n  @(a) r = a;\nend"),
        ("`@` inside a block", "always @(posedge clk) begin\n  @(posedge clk) r <= a;\nend"),
        ("`wait` inside a block", "initial begin\n  wait (a) r = a;\nend"),
        ("do not match", "initial begin\n  case (a)\n 0: r = 1;\nend"),
        ("never closed", "initial begin\n  r = 0;"),
        ("an event list that mixes", "always @(posedge clk, a) begin: l\n r = a;\nend"),
        ("an edge item that is not", "always @(posedge clk[0]) begin\n r <= a;\nend"),
        ("an edge item that is not", "always @(posedge global) begin\n r <= a;\nend"),
        ("an event control that cannot be read", "always @a begin\n r = a;\nend"),
        ("expected `@`", "always begin\n r = a;\nend"),
        ("is not a plain name", "reg global;"),
        ("is not a plain name", "wire logic;"),
        ("a call `x(...)`", "reg x(1);"),
    ],
)
def test_what_is_not_the_plain_single_module_is_refused(what: str, items: str) -> None:
    found = _module(items)
    assert found is not None, items
    assert what in found.what, (found.what, items)
    assert "@always_comb" not in found.what
    assert "plain single module" in found.what or "level" in found.what
    assert "MyHDL" in sv.plain_message(found)


def test_no_module_a_second_module_or_text_before_it_is_refused() -> None:
    for text in (
        "",
        "wire w;\nmodule m ();\nendmodule\n",
        "module m ();\n",
        "module m ();\nendmodule\nwire w;\n",
    ):
        found = sv.refusal([("m.v", text)], (), {"m.v"})
        assert found is not None and "plain single module" in found.what, text


def test_an_array_of_another_module_does_not_cover_this_modules_vector() -> None:
    """amulti: the first module's `mem` is a vector; the array is in a second module."""
    text = (
        "module a (clk);\ninput clk;\nreg [7:0] mem;\nreg [7:0] y;\n"
        "always @(mem[0], mem[1]) begin: pick\n y = mem;\nend\nendmodule\n"
        "module b (clk);\ninput clk;\nreg [7:0] mem [0:1];\nendmodule\n"
    )
    found = sv.refusal([("m.v", text)], (), {"m.v"})
    assert found is not None and "after the module" in found.what


def test_the_function_rule_and_the_module_rule_name_the_class_in_plain_words() -> None:
    found = _module("generate if (1) reg [7:0] mem [0:1]; endgenerate")
    assert found is not None
    assert "single module" in sv.plain_message(found) and "Let MyHDL write" in sv.plain_message(
        found
    )


# -- the ALWCOMBORDER pass: `always @*` written as `always_comb` ---------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("always @* begin\n y = a;\nend\n", "always_comb begin\n y = a;\nend\n"),
        ("always @(*) y = a;\n", "always_comb y = a;\n"),
        ("always\n  @ ( * )\n  y = a;\n", "always_comb\n\n  y = a;\n"),  # lines do not move
        ("always@* y = a;", "always_comb y = a;"),
        ("// always @* y = a;\nalways @(a) y = a;\n", None),  # a comment; a level list is not `@*`
        ('x = "always @*";\nalways @(posedge c) y <= a;\n', None),  # a string
        (
            "/* always @* */ always @* y = a;",
            "                always_comb y = a;",
        ),  # comment blanked
        ("alwaysx @* y = a;", None),
        ("always @(* ) y = a;\nalways @* z = a;\n", "always_comb y = a;\nalways_comb z = a;\n"),
    ],
)
def test_every_star_block_is_written_as_always_comb_and_nothing_else_moves(
    text: str, want: str | None
) -> None:
    got = sv.always_comb_copy(text)
    assert got == want
    if got is not None:
        assert got.count("\n") == text.count("\n")


def test_a_hierarchical_name_inside_a_function_is_refused() -> None:
    # hierk: Verilator folds `g.k` to a constant in one generate branch, Yosys reads a wire
    refused("function automatic [7:0] rd(input [7:0] d); rd = d ^ g.k; endfunction", "hierarchical")
    refused(
        "function automatic [7:0] rd(input [7:0] d);\n rd = d ^ top . k;\nendfunction",
        "hierarchical",
    )
    ok = "function automatic [7:0] rd(input [7:0] d); rd = d ^ 8'd1; endfunction\nassign w = g.k;"
    assert sv.refusal([("d.v", f"module m;\n{ok}\nendmodule\n")], None) is None  # outside: fine
    # S3: a function body ends at its own `endfunction` (non-greedy): `g.k` between two functions
    # is outside both
    two = f"{ok}\nfunction automatic [7:0] q(input [7:0] d); q = d; endfunction"
    assert sv.refusal([("d.v", f"module m;\n{two}\nendmodule\n")], None) is None


STRUCT = "typedef struct packed { logic [3:0] hi; logic [3:0] lo; } pair_t;\n"
HIER = "a hierarchical name inside a function (a dotted name that is not a member of an argument"


@pytest.mark.parametrize(
    "function",
    [
        "function automatic [7:0] swap(input pair_t p); swap = {p.lo, p.hi}; endfunction",  # n45
        "function automatic [3:0] f(input pair_t p, input [1:0] s); f = p . lo; endfunction",
        "function automatic [3:0] f; input pair_t p; f = p.hi; endfunction",  # non-ANSI
        "function automatic [3:0] f(input [7:0] x); pair_t q; q = x; f = q.lo; endfunction",
        "function automatic [3:0] f(input [7:0] x); begin pair_t q, r; q = x; r = q; f = r.hi;"
        " end endfunction",
        "function automatic [$clog2(8):0] f(input pair_t p); f = p.lo; endfunction",
    ],
)
def test_a_member_of_an_argument_or_local_inside_a_function_is_fine(function: str) -> None:
    ok(f"module m;\n{STRUCT}{function}\nendmodule\n")


@pytest.mark.parametrize(
    ("function", "extra"),
    [
        ("function automatic [3:0] f(input pair_t p); f = g.k; endfunction", ""),
        ("function automatic [3:0] f(input pair_t p); f = g\n.k; endfunction", ""),
        # `p` is an argument, but also a generate block: the tools may read `p.lo` differently
        (
            "function automatic [3:0] f(input pair_t p); f = p.lo; endfunction",
            "generate if (1) begin : p localparam lo = 1; end endgenerate",
        ),
        # ... or an instance name
        ("function automatic [3:0] f(input pair_t p); f = p.lo; endfunction", "sub p (.a(b));"),
        # a local declared deeper than the top of the body is not seen: refused, never passed
        (
            "function automatic [3:0] f(input [7:0] x); if (x[0]) begin pair_t q; q = x;"
            " f = q.lo; end else f = 0; endfunction",
            "",
        ),
        # the port list is the one after the name, not a `(` inside the range
        ("function automatic [$clog2(g)-1:0] f(input pair_t p); f = g.k; endfunction", ""),
        ("function automatic [3:0] f(input [7:0] x); f = f.x; endfunction", ""),  # its own name
        ("function automatic [3:0] f(input [7:0] x); f = m.x; endfunction", ""),  # the module
    ],
)
def test_a_dotted_name_that_is_not_a_member_of_an_argument_or_local_is_refused(
    function: str, extra: str
) -> None:
    refused(f"{STRUCT}{function}\n{extra}", HIER)


@pytest.mark.parametrize(
    "connection",
    [
        "sub u_sub (.clk(clk), .cfg(cfg), .q(r));",  # round 14 v73
        "sub u_sub (.cfg (cfg));",
        "sub u_sub (. cfg(cfg));",
        "sub u_sub (.\n  cfg\n  (cfg));",
        "sub u_sub [1:0] (.cfg(cfg));",
        "assign w = u_sub.cfg(ui_in);",  # the member of a dotted call: its head is the scope
    ],
)
def test_a_word_after_a_dot_is_a_port_or_member_not_a_scope(connection: str) -> None:
    """A named port connection `.cfg(cfg)` is not an instance or call named `cfg`: a function
    argument `cfg` of a packed struct type can still use `cfg.hi`."""
    function = "function automatic [3:0] pick(input pair_t cfg); pick = cfg.hi; endfunction"
    ok(f"module m;\n{STRUCT}{function}\n{connection}\nendmodule\n")


@pytest.mark.parametrize(
    "scope",
    [
        "sub cfg (.a(b));",  # an instance named cfg
        "sub cfg [3:0] (.a(b));",  # an array of instances
        "sub #(.W(4)) cfg (.a(b));",  # with a parameter override
        "assign w = cfg(ui_in);",  # a call
        "sub u_sub (.cfg(cfg));\nsub cfg (.a(b));",  # a port connection does not hide one
    ],
)
def test_an_instance_or_call_of_the_argument_name_is_still_a_scope(scope: str) -> None:
    function = "function automatic [3:0] pick(input pair_t cfg); pick = cfg.hi; endfunction"
    refused(f"{STRUCT}{function}\n{scope}", HIER)


def test_the_reviewers_struct_argument_with_a_same_named_port_is_not_refused() -> None:
    """Round 14 v73 (the equivalence fixture `structport`): a sub-module port `.cfg(cfg)` and a
    function argument `cfg` of a packed struct type that reads `cfg.hi` and `cfg.lo`."""
    path = REPO_ROOT / "tests" / "fixtures" / "equiv_designs" / "structport" / "verilog.v"
    assert sv.refusal([("structport.v", path.read_text())]) is None


def test_a_glued_begin_after_a_star_block_gets_a_blank() -> None:
    assert sv.always_comb_copy("always@(*)begin y = a; end") == "always_comb begin y = a; end"


def test_an_order_across_loop_iterations_is_not_a_token_refusal() -> None:
    """vloopit: hand-written `always @*` that reads `t` in iteration 0 and writes it in 1. A
    simulator keeps the old t; Verilator's ALWCOMBORDER cannot see an order that only exists across
    iterations. Not refused by the tokens: the path-sensitive walk on Verilator's tree refuses it
    (`equiv_tree.early_read`, a loop pass that reads `t` before it writes it); the MyHDL parser
    refuses the same shape."""
    path = REPO_ROOT / "tests" / "fixtures" / "equiv_designs" / "vloopit" / "verilog.v"
    assert sv.refusal([("vloopit.v", path.read_text())]) is None


# -- loops: an iteration may read what another one writes ----------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        # the reviewer's gloopit / mloopit: iteration 0 reads t, iteration 1 writes it
        "integer i;\n y = 0;\n for (i = 0; i < 2; i = i + 1) begin\n  if (i == 1) t = cnt;\n"
        "  else y = t;\n end",
        "integer i;\n for (i = 0; i < 2; i = i + 1) begin\n  case (i)\n   1: t = cnt;\n"
        "   default: y = t;\n  endcase\n end",
        "integer i;\n y = 0;\n for (i = 0; i < 4; i = i + 1) begin\n  if (a[i]) begin\n"
        "   if (b[i]) t = 1;\n  end\n  else y = t;\n end",
    ],
)
def test_a_signal_that_a_loop_both_reads_and_writes_is_refused(body: str) -> None:
    found = _lvl(body, "a, b, cnt, t, y, z")
    assert found is not None, body
    assert "in a for loop" in found.what and "before the loop" in found.what, found.what
    assert found.label == "l" and "@always_comb" in sv.plain_message(found)


@pytest.mark.parametrize(
    "body",
    [
        "integer i;\n y = 0;\n for (i = 0; i < 8; i = i + 1) y = y + a[i];",  # the accumulator
        "integer i;\n integer n;\n n = 0;\n for (i = 0; i < 8; i = i + 1) begin\n"
        "  if (a[i]) n = n + 1;\n end\n y = n;",  # lfor.v
        "integer i;\n t = a;\n y = 0;\n for (i = 0; i < 2; i = i + 1) begin\n  y = y + t;\n"
        "  t = t + 1;\n end",  # t was written before the loop
        "integer i;\n for (i = 0; i < 4; i = i + 1) begin\n  y[i] = a[i];\n end",
        "integer i;\n y = 0;\n for (i = 0; i < 2; i = i + 1) begin\n  if (a[i]) y = y | b;\n end"
        "\n z = y;",
        "j = 0;\n for (k = 0; k < 2; k = k + 1) begin\n  y = a;\n end",
    ],
)
def test_the_normal_accumulator_and_loops_over_signals_written_first_are_accepted(
    body: str,
) -> None:
    assert _lvl(body, "a, b, t, y, z, j, k") is None, body
