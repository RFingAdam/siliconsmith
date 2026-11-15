# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The simulator's (Icarus) constants, read from text Icarus 12 wrote for real designs (no
Icarus needed). `tests/integration/test_equiv.py` runs the real compiler."""

from __future__ import annotations

import pytest

from siliconsmith.worker import equiv_icarus as ei

OURS = ["project.v", "core.v"]
TABLE = (
    ':file_names 5;\n    "N/A";\n    "<interactive>";\n    "-";\n    "project.v";\n    "core.v";\n'
)


def vvp(*params: str) -> str:
    """A `.vvp` text with these parameter lines (Icarus writes them inside the scopes)."""
    head = (
        '#! /usr/bin/vvp\n:ivl_version "12.0 (stable)";\n'
        'S_0x1 .scope module, "tt_um_core" "tt_um_core" 3 7;\n'
        ' .timescale 0 0;\nS_0x2 .scope module, "core" "core" 3 18, 4 2 0, S_0x1;\n'
    )
    return head + "".join(f"{p}\n" for p in params) + "    %end;\n" + TABLE


# round 14 xsel: `localparam [0:0] Q = A[I];` with a 4-bit A and I = 6
XSEL = vvp(
    'P_0x5790c56fe620 .param/l "A" 1 4 6, C4<1010>;',
    'P_0x5790c56fe660 .param/l "I" 1 4 7, +C4<00000000000000000000000000000110>;',
    'P_0x5790c56fe6a0 .param/l "Q" 1 4 8, C4<x>;',
)
# round 14 qf2: `localparam integer Q = pick(6);`, a constant function that reads v[6] of a 4-bit v
QF2 = vvp(
    'P_0x5bf72bb93c00 .param/l "Q" 1 4 10, +C4<0000000000000000000000000000000x>;',
    'P_0x5bf72bb93c40 .param/l "Z" 1 4 5, +C4<00000000000000000000000000000000>;',
)
# a genvar loop (one `g` per pass) and the other kinds of value Icarus writes
CLEAN = vvp(
    'P_0x5e59f6856760 .param/l "g" 1 4 8, +C4<00>;',
    'P_0x5e59f6856c60 .param/l "g" 1 4 8, +C4<01>;',
    'P_0x630eaa45d490 .param/l "N" 0 4 1, +C4<00000000000000000000000000000011>;',
    'P_0x630eaa45d4d0 .param/l "NEG" 1 4 6, +C4<11111101>;',
    'P_0x630eaa45d510 .param/real "R" 1 4 3, Cr<m6000000000000000gfc2>; value=1.50000',
    'P_0x630eaa45d550 .param/str "S" 1 4 2, "hi;\\042x";',
)


@pytest.mark.parametrize(("text", "name", "line"), [(XSEL, "Q", 8), (QF2, "Q", 10)])
def test_a_parameter_the_simulator_reads_as_x_is_named(text: str, name: str, line: int) -> None:
    """Verilator folds both to a defined value; Icarus (and Yosys) read x."""
    got = ei.x_parameter(text, OURS)
    assert got is not None and (got.file, got.line) == ("core.v", line)
    assert f"gives the parameter `{name}` a value with an x or z bit" in got.sentence
    assert "Give it a defined value" in got.sentence


def test_defined_values_strings_reals_and_genvars_pass() -> None:
    assert ei.x_parameter(CLEAN, OURS) is None


def test_a_z_bit_an_escaped_name_and_the_first_by_file_then_line() -> None:
    text = vvp(
        'P_0x3 .param/l "late" 1 4 9, C4<1x>;',
        'P_0x4 .param/l "w\\"e.ird" 1 4 4, C4<10z1>;',
        'P_0x5 .param/l "wrap" 1 3 20, C4<0x>;',  # project.v comes first
    )
    got = ei.x_parameter(text, OURS)
    assert got is not None and (got.file, got.line) == ("project.v", 20)
    got = ei.x_parameter(text, ["core.v"])
    assert got is not None and got.line == 4 and '`w"e.ird`' in got.sentence


def test_a_parameter_of_another_file_is_not_judged() -> None:
    text = vvp('P_0x3 .param/l "Q" 1 2 4, C4<x>;')  # file 2 is "-"
    assert ei.x_parameter(text, OURS) is None


@pytest.mark.parametrize(
    "row",
    [
        'P_0x3 .param/l "Q" 1 4 8 C4<x>;',  # no comma
        'P_0x3 .param/l "Q" 1 4 8, C4<2>;',  # not a four-state digit
        'P_0x3 .param/l "Q" 1 4 8, Cr<m1g1>;',  # a real as a vector
        'P_0x3 .param/l "Q" 1 9 8, C4<0>;',  # a file that is not in the table
        'P_0x3 .param/odd "Q" 1 4 8, C4<0>;',  # a kind this module does not know
    ],
)
def test_a_parameter_line_that_cannot_be_read_fails_closed(row: str) -> None:
    with pytest.raises(ei.IcarusError):
        ei.x_parameter(vvp(row), OURS)


def test_a_missing_or_short_file_table_fails_closed() -> None:
    with pytest.raises(ei.IcarusError):
        ei.x_parameter(XSEL.replace(":file_names 5;", ":file_names 6;"), OURS)
    with pytest.raises(ei.IcarusError):
        ei.x_parameter(XSEL.split(":file_names")[0], OURS)


# What `iverilog -g2012 -Wselect-range` printed for round 14 xseld and a set of probes
WARNINGS = """\
core.v:16: warning: @* is sensitive to all 2 words in array 'tt'.
core.v:8: warning: Constant bit select [6] is after A[3:0].
core.v:8:        : Replacing select with a constant 1'bx.
"""
PROBES = [
    "Part select A[5:2] is selecting after the parameter A[3:0].",
    "A[3+:4] is selecting after vector.",
    "Constant bit select [8] is after vector a[7:0].",
    "returning 'bx for out of bounds array access mem[5].",
    "returning 'bx for undefined array access tt[<M=32'sb0x, wid=32>].",
    "ignoring out of bounds l-value array access mem[5].",
]


def test_an_out_of_range_constant_select_is_named_with_the_simulators_words() -> None:
    got = ei.x_select(WARNINGS, OURS)
    assert got is not None and (got.file, got.line) == ("core.v", 8)
    assert "Constant bit select [6] is after A[3:0]." in got.sentence
    assert "Keep constant indexes inside the declared range" in got.sentence


@pytest.mark.parametrize("text", PROBES)
def test_every_out_of_range_warning_counts(text: str) -> None:
    got = ei.x_select(f"core.v:12: warning: {text}\n", OURS)
    assert got is not None and got.line == 12


def test_other_warnings_and_other_files_do_not_count() -> None:
    other = (
        "core.v:16: warning: @* is sensitive to all 2 words in array 'tt'.\n"
        "core.v:7: warning: @* found no sensitivities so it will never trigger.\n"
        "lib.v:3: warning: Constant bit select [6] is after A[3:0].\n"
        "core.v:5: sorry: constant selects in always_* processes are not currently supported.\n"
    )
    assert ei.x_select(other, OURS) is None
    first = "core.v:9: warning: A[3+:4] is selecting after vector.\n" + WARNINGS
    got = ei.x_select(first, OURS)
    assert got is not None and got.line == 8


def test_a_warning_in_the_first_of_our_files_counts_and_file_order_comes_before_line() -> None:
    """The wrapper (`project.v`, the first of `ours`) is checked too, and named first."""
    got = ei.x_select(
        "core.v:8: warning: Constant bit select [6] is after A[3:0].\n"
        "project.v:30: warning: Constant bit select [9] is after vector ui_in[7:0].\n",
        OURS,
    )
    assert got is not None and (got.file, got.line) == ("project.v", 30)
    assert "Constant bit select [9] is after vector ui_in[7:0]." in got.sentence


# -- round 15: the simulator's values against Verilator's ---------------------------------------

# c02ifnot: `localparam integer M = pick(4'b1010, 6);` with `if (!v[k]) pick = 0; else pick = 1;`
# Icarus reads v[6] as x, `!x` is x, the `if` takes the else branch: 1, with no x bit and no
# warning. Verilator (and Yosys) read v[6] as 0: 0.
LAUNDERED = vvp('P_0x5f6c3b2fe620 .param/l "M" 1 4 11, +C4<00000000000000000000000000000001>;')


CORE = ("tt_um_core", "core")  # the path of the scope `vvp` puts every parameter in


def test_the_simulators_values_once_per_instance_and_pass() -> None:
    """With the path of the scope each one is in, and with its sign (`+C4<...>`)."""
    got = ei.parameter_values(CLEAN, OURS)
    assert got == {
        ("core.v", 8, "g"): [(CORE, 0), (CORE, 1)],
        ("core.v", 1, "N"): [(CORE, 3)],
        ("core.v", 6, "NEG"): [(CORE, -3)],  # +C4<11111101>: signed
        ("core.v", 3, "R"): None,
        ("core.v", 2, "S"): None,
    }
    assert ei.parameter_values(QF2, OURS)[("core.v", 10, "Q")] is None  # an x bit
    assert ei.parameter_values(QF2, ["project.v"]) == {}


# round 16 p11sw64: `for (genvar g = 0; g < 2; g++) begin : b localparam integer M = (g == 0) ?
# pick(4'b1010, 64) : 1 - pick(4'b1010, 64); ... end` as Icarus 12 wrote it: one scope per loop
# pass (b[0], b[1]) inside `core`, the function's scope after them, and code that returns to a
# scope declared further down (`.scope S_0x9;` before `S_0x9 .scope ...`).
SWAPPED = (
    '#! /usr/bin/vvp\n:ivl_version "12.0 (stable)";\n'
    'S_0x1 .scope package, "$unit" "$unit" 2 1;\n'
    'S_0x2 .scope module, "tt_um_core" "tt_um_core" 3 7;\n'
    'S_0x3 .scope module, "core" "core" 3 18, 4 2 0, S_0x2;\n'
    'S_0x4 .scope generate, "b[0]" "b[0]" 4 11, 4 11 0, S_0x3;\n'
    'P_0x10 .param/l "M" 1 4 12, +C4<00000000000000000000000000000001>;\n'
    'P_0x11 .param/l "g" 1 4 11, +C4<00>;\n'
    'S_0x5 .scope generate, "b[1]" "b[1]" 4 11, 4 11 0, S_0x3;\n'
    'P_0x12 .param/l "M" 1 4 12, +C4<00000000000000000000000000000000>;\n'
    'P_0x13 .param/l "g" 1 4 11, +C4<01>;\n'
    "    .scope S_0x9;\n"
    'S_0x6 .scope autofunction.vec4.u32, "pick" "pick" 4 7, 4 7 0, S_0x3;\n'
    'S_0x9 .scope autobegin, "$ivl_for_loop0" "$ivl_for_loop0" 4 4, 4 4 0, S_0x6;\n'
    "    .scope S_0x3;\n    %end;\n" + TABLE
)
B0, B1 = (*CORE, "b[0]"), (*CORE, "b[1]")
M = ("core.v", 12, "M")


def test_every_value_has_the_path_of_its_scope() -> None:
    got = ei.parameter_values(SWAPPED, OURS)
    assert got == {M: [(B0, 1), (B1, 0)], ("core.v", 11, "g"): [(B0, 0), (B1, 1)]}


def test_values_swapped_between_loop_passes_or_instances_are_named_with_the_pass() -> None:
    """Round 16: Verilator gave b[0] M = 0 and b[1] M = 1. As a multiset both tools have {0, 1},
    but every pass indexes another bit in a simulator than in the gates."""
    simulator = ei.parameter_values(SWAPPED, OURS)
    got = ei.disagreement(simulator, {M: [(B0, 0), (B1, 1)]}, OURS)
    assert got is not None and (got.file, got.line) == ("core.v", 12)
    assert (
        "gives the parameter `M` the value 1 in a simulator (Icarus) but 0 in Verilator's "
        "elaboration (in `tt_um_core.core.b[0]`)" in got.sentence
    )
    assert "keep every select inside the declared range" in got.sentence
    assert ei.disagreement(simulator, {M: [(B1, 0), (B0, 1)]}, OURS) is None  # any order


def test_a_parameter_the_simulator_computes_differently_from_verilator_is_named() -> None:
    simulator = ei.parameter_values(LAUNDERED, OURS)
    got = ei.disagreement(simulator, {("core.v", 11, "M"): [(CORE, 0)]}, OURS)
    assert got is not None and (got.file, got.line) == ("core.v", 11)
    assert (
        "gives the parameter `M` the value 1 in a simulator (Icarus) but 0 in Verilator's "
        "elaboration" in got.sentence
    )
    assert "keep every select inside the declared range" in got.sentence
    assert ei.disagreement(simulator, {("core.v", 11, "M"): [(CORE, 1)]}, OURS) is None


def test_values_are_compared_in_full_as_numbers_with_their_sign() -> None:
    """Round 16 brep and p12rep64: `localparam V = {(pick(4'b1010, 6) + 1){1'b1}};` is 2'b11 to
    Icarus and 1'h1 to Verilator: the bit both widths hold agrees, the numbers (3, 1) do not."""
    key = ("core.v", 7, "V")

    def differ(bits: str, theirs: int) -> bool:
        line = f'P_0x3 .param/l "V" 1 4 7, {bits};'
        return ei.disagreement(ei.parameter_values(vvp(line), OURS), {key: [(CORE, theirs)]},
                               OURS) is not None  # fmt: skip

    assert differ("C4<11>", 1)  # brep: 3 against 1
    assert not differ("C4<11>", 3)
    assert not differ("C4<000000000000000000000000000000110>", 6)  # `4'd5 + 1`, 33 bits wide
    assert differ("C4<100000000>", 0)  # `8'hff + 8'h01`: Icarus 9'h100, Verilator 8'h00
    assert not differ("+C4<111111111111111111111111111111111>", -1)  # -1, 33 bits signed
    assert differ("C4<111111111111111111111111111111111>", -1)  # unsigned: 2**33 - 1
    assert differ("+C4<1101>", 13)  # -3, not 13
    assert not differ("+C4<0101>", 5)
    # an untyped `(1 << 32) - 1` and `1 << 31`: Icarus makes them wider and positive, Verilator
    # keeps 32 signed bits; the low 32 bits agree, but `M > 0` does not
    assert differ("C4<0" + "1" * 32 + ">", -1)
    assert differ("C4<01" + "0" * 31 + ">", -(1 << 31))
    assert not differ("C4<" + "1" * 32 + ">", (1 << 32) - 1)  # `localparam [31:0]`
    wide = ei.parameter_values(vvp('P_0x3 .param/l "V" 1 4 7, C4<0' + "1" * 32 + ">;"), OURS)
    got = ei.disagreement(wide, {key: [(CORE, -1)]}, OURS)
    assert got is not None and "the value 4294967295 in a simulator (Icarus) but -1" in got.sentence
    assert "give a localparam that can overflow a type or a size" in got.sentence


def test_paths_that_cannot_be_paired_pass_only_with_one_value_in_both() -> None:
    """A block without a name: Icarus calls it `$unm_blk_2`, Verilator `unnamedblk1`."""
    key = ("core.v", 5, "P")
    a, b, c = (*CORE, "$unm_blk_2"), (*CORE, "unnamedblk1"), (*CORE, "x")

    def differ(mine: list[tuple[tuple[str, ...], int]], theirs: list) -> str | None:
        got = ei.disagreement({key: mine}, {key: theirs}, OURS)
        return None if got is None else got.sentence

    assert differ([(a, 4)], [(b, 4)]) is None
    assert differ([(a, 4), (c, 4)], [(b, 4)]) is None  # every instance has 4 in both
    assert differ([(a, 4)], [(a, 4), (a, 4)]) is None  # a path twice: not paired, one value
    # a path twice with two values: never paired by its last value
    assert differ([(a, 4), (a, 5)], [(a, 5)]) is not None
    assert differ([(a, 5)], [(a, 4), (a, 5)]) is not None
    got = differ([(a, 4)], [(b, 5)])
    assert got is not None and "the value 4 in a simulator (Icarus) but 5 in Verilator" in got
    got = differ([(a, 4), (c, 5)], [(b, 4), (c, 5)])
    assert got is not None and "the values 4, 5 in its instances and loop passes" in got
    assert "cannot pair the simulator's (Icarus) instances (`tt_um_core.core.$unm_blk_2`" in got
    assert "Give every block that holds this parameter a name" in got
    assert "keep every select" not in got


@pytest.mark.parametrize("order", [["1", "x"], ["x", "1"], ["x", "x"]])
def test_an_x_in_one_instance_makes_the_whole_parameter_uncompared(order: list[str]) -> None:
    """Whichever instance comes first: one x is enough (`x_parameter` refuses it)."""
    rows = [
        f'S_0x{i + 5} .scope module, "u{i}" "sub" 4 7, 4 14 0, S_0x2;\n'
        f'P_0x{i + 5} .param/l "P" 1 4 5, C4<{bit}>;'
        for i, bit in enumerate(order)
    ]
    assert ei.parameter_values(vvp(*rows), OURS) == {("core.v", 5, "P"): None}


def test_a_parameter_only_one_tool_lists_or_without_a_plain_value_is_not_compared() -> None:
    key = ("core.v", 5, "P")
    for mine, theirs in [(None, [(CORE, 0)]), ([(CORE, 1)], None), ([(CORE, 1)], [])]:
        assert ei.disagreement({key: mine}, {key: theirs}, OURS) is None
    assert ei.disagreement({key: [(CORE, 1)]}, {}, OURS) is None


def test_the_first_disagreement_by_file_order_then_line_is_named_with_every_value() -> None:
    five = [((*CORE, f"u{i}"), 1) for i in range(5)]
    simulator = {
        ("core.v", 3, "A"): [(CORE, 1)],
        ("project.v", 9, "W"): five,
        ("core.v", 2, "B"): [(CORE, 1)],
    }
    verilator = {
        ("core.v", 3, "A"): [(CORE, 0)],
        ("project.v", 9, "W"): [((*CORE, "v"), 2)],
        ("core.v", 2, "B"): [(CORE, 0)],
    }
    got = ei.disagreement(simulator, verilator, OURS)
    assert got is not None and (got.file, got.line) == ("project.v", 9)
    assert "the value 1, 1, 1, 1, ... in a simulator (Icarus) but 2 in Verilator's" in got.sentence
    got = ei.disagreement(simulator, verilator, ["core.v"])
    assert got is not None and (got.file, got.line) == ("core.v", 2)


@pytest.mark.parametrize(
    "rows",
    [
        ['S_0x7 .scope module "core" "core" 3 18;'],  # no comma
        ['S_0x7 .scope module, "core" "core" 3 18, 4 2 0, S_0x8;'],  # a parent never declared
        [  # each the other's parent
            'S_0x7 .scope module, "a" "a" 3 18, 4 2 0, S_0x8;',
            'S_0x8 .scope module, "b" "b" 3 18, 4 2 0, S_0x7;',
        ],
        ['S_0x1 .scope module, "again" "again" 3 7;'],  # a label declared twice
    ],
)
def test_a_scope_that_cannot_be_read_fails_closed(rows: list[str]) -> None:
    with pytest.raises(ei.IcarusError):
        ei.parameter_values(vvp(*rows), OURS)


def test_a_parameter_outside_every_scope_fails_closed() -> None:
    lone = 'P_0x3 .param/l "Q" 1 4 8, C4<0>;\n' + TABLE
    with pytest.raises(ei.IcarusError, match="outside every scope"):
        ei.parameter_values(lone, OURS)
    with pytest.raises(ei.IcarusError, match="outside every scope"):  # a scope never declared
        ei.parameter_values(vvp("    .scope S_0x9;", 'P_0x3 .param/l "Q" 1 4 8, C4<0>;'), OURS)
    declared_later = 'S_0x9 .scope module, "t" "t" 3 7;\n' + TABLE
    later = "    .scope S_0x9;\n" + lone.replace(TABLE, declared_later)
    assert ei.parameter_values(later, OURS) == {("core.v", 8, "Q"): [(("t",), 0)]}


def test_a_parent_declared_after_its_child_still_gives_the_path() -> None:
    text = (
        'S_0x7 .scope generate, "g" "g" 4 3, 4 3 0, S_0x8;\n'
        'P_0x3 .param/l "Q" 1 4 8, C4<1>;\n'
        'S_0x8 .scope module, "top" "top" 3 1;\n' + TABLE
    )
    assert ei.parameter_values(text, OURS) == {("core.v", 8, "Q"): [(("top", "g"), 1)]}


# -- round 16: a call that can give x in a value that is not compared ---------------------------


def test_a_call_in_a_value_that_is_not_compared_is_refused() -> None:
    """Round 16 breal: `localparam real R = pick(4'b1010, 6);` is 1.0 to Icarus and 0.0 to
    Verilator; a real is not compared, so the call that `constant_call` lets through there is
    refused (`equiv_tree.valued_calls` names R)."""
    key = ("core.v", 7, "R")
    real = vvp('P_0x3 .param/real "R" 1 4 7, Cr<m4000000000000000gfc2>; value=1.00000')
    simulator = ei.parameter_values(real, OURS)
    got = ei.uncompared(simulator, {key: None}, frozenset({key}), OURS)
    assert got is not None and (got.file, got.line) == ("core.v", 7)
    assert "gives the parameter `R` a value with a call of a function that can give" in got.sentence
    assert "Compute the call in an integer localparam first" in got.sentence
    assert ei.uncompared(simulator, {key: None}, frozenset(), OURS) is None  # no such call


@pytest.mark.parametrize(
    ("mine", "theirs", "refused"),
    [
        ([(CORE, 1)], [(CORE, 1)], False),  # compared by `disagreement`
        ([(CORE, 1)], [], True),  # Verilator has no instance of it
        (None, [(CORE, 1)], True),  # a string or real to the simulator
        ([(CORE, 1)], None, True),
        ("absent", [(CORE, 1)], True),  # only Verilator lists it
        ([(CORE, 1)], "absent", True),
        ("absent", [], False),  # neither tool instantiates it
        ("absent", "absent", False),
    ],
)
def test_only_a_value_that_both_tools_give_in_every_instance_is_compared(
    mine: object, theirs: object, refused: bool
) -> None:
    key = ("core.v", 7, "R")
    simulator = {} if mine == "absent" else {key: mine}
    verilator = {} if theirs == "absent" else {key: theirs}
    got = ei.uncompared(simulator, verilator, frozenset({key}), OURS)  # type: ignore[arg-type]
    assert (got is not None) is refused
    assert ei.uncompared(simulator, verilator, frozenset({key}), ["project.v"]) is None


def test_the_first_uncompared_value_by_file_order_then_line() -> None:
    keys = frozenset({("core.v", 9, "B"), ("project.v", 30, "W"), ("core.v", 4, "A")})
    got = ei.uncompared({}, {k: None for k in keys}, keys, OURS)
    assert got is not None and (got.file, got.line) == ("project.v", 30)
    got = ei.uncompared({}, {k: None for k in keys}, keys, ["core.v"])
    assert got is not None and (got.file, got.line) == ("core.v", 4)


# -- round 23: the same value with another width or sign -----------------------------------------

M8 = ("core.v", 6, "M")


def test_the_simulators_shapes_follow_its_values() -> None:
    """The width is the number of bits Icarus writes, the sign its `+`; None where
    `parameter_values` gives None, with the same paths."""
    assert ei.parameter_shapes(CLEAN, OURS) == {
        ("core.v", 8, "g"): [(CORE, (2, True)), (CORE, (2, True))],
        ("core.v", 1, "N"): [(CORE, (32, True))],
        ("core.v", 6, "NEG"): [(CORE, (8, True))],
        ("core.v", 3, "R"): None,
        ("core.v", 2, "S"): None,
    }
    assert ei.parameter_shapes(QF2, OURS)[("core.v", 10, "Q")] is None  # an x bit
    assert ei.parameter_shapes(SWAPPED, OURS)[M] == [(B0, (32, True)), (B1, (32, True))]
    # wcat: `localparam M = 8'hff + 8'h00;` is 9 bits wide in Icarus
    wcat = vvp('P_0x3 .param/l "M" 1 4 6, C4<011111111>;')
    assert ei.parameter_values(wcat, OURS) == {M8: [(CORE, 255)]}
    assert ei.parameter_shapes(wcat, OURS) == {M8: [(CORE, (9, False))]}


def shaped(mine: tuple[int, bool], theirs: tuple[int, bool]) -> str | None:
    got = ei.shape_disagreement({M8: [(CORE, mine)]}, {M8: [(CORE, theirs)]}, OURS)
    return None if got is None else got.sentence


@pytest.mark.parametrize(
    ("mine", "theirs", "refused"),
    [
        ((8, False), (8, False), False),  # `localparam [7:0] M = 8'hff + 8'h00;`
        ((32, True), (32, True), False),  # `W`, `$clog2(W)`, `W / 2`
        ((9, False), (8, False), True),  # wcat: `8'hff + 8'h00`
        ((5, False), (4, False), True),  # `4'd3 + 4'd4`
        ((32, True), (31, True), True),  # the bound: Verilator under 32 bits
        ((32, False), (31, False), True),
        ((33, True), (32, True), False),  # `W - 1`, `W + 1`: Verilator 32 bits
        ((33, False), (32, False), False),  # `8'hff + 1`, `32'hffff_ffff + 32'h0` (w32cat)
        ((64, True), (32, True), False),  # `W * 2`
        ((96, True), (32, True), False),  # `Q * Q * Q`
        ((40, False), (33, False), True),  # wider than 32 in both: only exactly 32 passes
        ((41, False), (40, False), True),  # w01, w40sum: `40'hff_ffff_ffff + 40'h0`
        ((65, True), (64, True), True),  # a sized 64-bit sum
        ((8, True), (8, False), True),  # another sign, the same width
        ((32, False), (32, True), True),
        ((33, False), (32, True), True),  # wider in Icarus, but another sign
        ((8, False), (9, False), True),  # narrower in Icarus
        ((32, True), (33, True), True),  # narrower in Icarus, also from 32 bits up
    ],
)
def test_a_value_with_another_width_or_sign_is_refused_unless_verilator_has_exactly_32_bits(
    mine: tuple[int, bool], theirs: tuple[int, bool], refused: bool
) -> None:
    assert (shaped(mine, theirs) is not None) is refused


def test_the_refusal_names_both_widths_why_and_the_fix() -> None:
    got = ei.shape_disagreement({M8: [(CORE, (9, False))]}, {M8: [(CORE, (8, False))]}, OURS)
    assert got is not None and (got.file, got.line) == ("core.v", 6)
    assert got.sentence.startswith(
        "the RTL (core.v, line 6) gives the parameter `M` the same value but 9 unsigned bits in "
        "a simulator (Icarus) and 8 unsigned bits in Verilator's elaboration and the gates (in "
        "`tt_um_core.core`): a concatenation, `$bits`, a reduction or `~` sees that, so the "
        "check cannot vouch for the design. Icarus works out an untyped parameter's value "
        "without losing bits, so it is wider there"
    )
    assert "a select outside the range in a function that a range calls" in got.sentence
    assert got.sentence.endswith(
        "Give it a type or a size (`localparam [7:0] M = 8'hff + 8'h00;`) and keep every select "
        "inside the declared range"
    )
    # rangecall (round 16): a typed range of 2 bits in Icarus, 1 in Verilator
    one = shaped((2, False), (1, False))
    assert (
        one is not None and "2 unsigned bits in a simulator (Icarus) and 1 unsigned bit in" in one
    )
    signed = shaped((8, True), (8, False))
    assert signed is not None and "8 signed bits in a simulator (Icarus) and 8 unsigned" in signed
    # round 24: each kind of difference names its own cause, never "wider" for a narrower one
    assert "The two tools give it another sign (Icarus makes `$bits` unsigned)" in signed
    assert "without losing bits" not in signed and "genvar" not in signed
    narrower = shaped((4, True), (32, True))  # d41genshift: `localparam LO = i << 2;`
    assert (
        narrower is not None and "4 signed bits in a simulator (Icarus) and 32 signed" in narrower
    )
    assert (
        "Icarus can make it narrower (a value computed from a genvar gets only the bits it "
        "needs there: for `localparam LO = i << 2;` in a generate loop write `i * 4` or "
        "`localparam integer LO = i << 2;`), and a select outside the range" in narrower
    )
    assert "without losing bits" not in narrower and "another sign" not in narrower
    assert "genvar" not in str(one) and "another sign" not in str(one)


def test_shapes_are_paired_by_path_like_values() -> None:
    """Each instance against the same instance in Verilator: two instances whose widths both
    tools agree on pass, though 32 bits in one tool's u0 against 33 in the other's u1 would not;
    the first instance that differs is named."""
    u0, u1 = (*CORE, "u0"), (*CORE, "u1")
    mine = [(u0, (32, True)), (u1, (33, True))]
    assert (
        ei.shape_disagreement({M8: mine}, {M8: [(u1, (33, True)), (u0, (32, True))]}, OURS) is None
    )
    got = ei.shape_disagreement(
        {M8: [(u0, (8, False)), (u1, (9, False))]}, {M8: [(u0, (8, False)), (u1, (8, False))]}, OURS
    )
    assert got is not None and "(in `tt_um_core.core.u1`)" in got.sentence


def test_shapes_that_cannot_be_paired_must_fit_in_every_combination() -> None:
    """A block without a name (`$unm_blk_2` against `unnamedblk1`), or a path twice: every shape
    of the simulator against every shape of Verilator."""
    a, b, c = (*CORE, "$unm_blk_2"), (*CORE, "unnamedblk1"), (*CORE, "x")

    def misfit(mine: list, theirs: list) -> str | None:
        got = ei.shape_disagreement({M8: mine}, {M8: theirs}, OURS)
        return None if got is None else got.sentence

    assert misfit([(a, (8, False))], [(b, (8, False))]) is None
    assert misfit([(a, (33, True)), (c, (64, True))], [(b, (32, True))]) is None
    got = misfit([(a, (9, False))], [(b, (8, False))])
    assert got is not None and "(in one of its instances)" in got
    # each pair would fit by path, but the check cannot tell which is which: 32 bits in the
    # simulator against 33 in Verilator is narrower
    assert misfit([(a, (32, True)), (c, (33, True))], [(b, (32, True)), (c, (33, True))])
    assert misfit([(a, (32, True)), (a, (33, True))], [(a, (32, True))]) is None  # a path twice
    assert misfit([(a, (32, True))], [(a, (32, True)), (a, (31, True))]) is not None


def test_a_parameter_only_one_tool_lists_or_without_a_plain_value_has_no_shape_compared() -> None:
    for mine, theirs in [(None, [(CORE, (8, False))]), ([(CORE, (9, False))], None),
                         ([(CORE, (9, False))], []), ([], [(CORE, (8, False))])]:  # fmt: skip
        assert ei.shape_disagreement({M8: mine}, {M8: theirs}, OURS) is None
    assert ei.shape_disagreement({M8: [(CORE, (9, False))]}, {}, OURS) is None
    assert ei.shape_disagreement({}, {M8: [(CORE, (8, False))]}, OURS) is None
    nine, eight = {M8: [(CORE, (9, False))]}, {M8: [(CORE, (8, False))]}
    assert ei.shape_disagreement(nine, eight, ["project.v"]) is None  # another file


def test_the_first_shape_disagreement_by_file_order_then_line() -> None:
    keys = [("core.v", 9, "B"), ("project.v", 30, "W"), ("core.v", 4, "A")]
    simulator = {k: [(CORE, (9, False))] for k in keys}
    verilator = {k: [(CORE, (8, False))] for k in keys}
    got = ei.shape_disagreement(simulator, verilator, OURS)
    assert got is not None and (got.file, got.line) == ("project.v", 30)
    got = ei.shape_disagreement(simulator, verilator, ["core.v"])
    assert got is not None and (got.file, got.line) == ("core.v", 4)
