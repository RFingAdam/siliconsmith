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
