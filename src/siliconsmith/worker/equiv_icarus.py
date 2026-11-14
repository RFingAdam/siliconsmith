# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the simulator (Icarus Verilog) makes of the constants of the RTL.

The purity and read-before-write checks read Verilator's tree, and the proof reads Yosys's
elaboration. Neither shows every x that a simulator computes while it elaborates the design:
Verilator folds a constant select outside the range (`A[6]` of a 4-bit `A`) to 0 and gives a
constant function that returns x some value, while Yosys and Icarus read x. A generate branch
chosen by such a value can then differ between the tools.

Icarus is the simulator that `run_tests` uses for gate-level runs. `iverilog -g2012` writes the
elaborated design as text (`.vvp`): every parameter, localparam and genvar, once per module
instance and loop pass, with its source file, line and value bit by bit (`C4<01xz>`). With
`-Wselect-range` it also warns about every constant select or array index outside the range.
Two questions, answered from that text (no subprocess here, so the rules can be tested by hand):

* `x_parameter`: a parameter, localparam or genvar of our files whose value has an x or z bit;
* `x_select`: a warning that a constant select or index of our files is outside the range (the
  simulator reads x there, Yosys reads x or a value of its own, and the gates some value).

Fail closed: text this module cannot read raises `IcarusError`, which the caller turns into a
refusal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class IcarusError(Exception):
    """The `.vvp` text is not in a shape this module understands."""


@dataclass(frozen=True)
class Finding:
    """Where the simulator found an x, and the sentence that explains it."""

    file: str
    line: int
    sentence: str


_FILE_TABLE = re.compile(r"^:file_names (\d+);\s*$")
_FILE_NAME = re.compile(r'^\s*"(.*)";\s*$')
# `P_0x... .param/l "NAME" <local> <file> <line>, <value>;` (a real carries a comment after `;`)
_PARAM_LINE = re.compile(r"^\S+\s+\.param/")
_PARAM = re.compile(r'^\S+\s+\.param/(\w+)\s+"((?:[^"\\]|\\.)*)"\s+(\d+)\s+(\d+)\s+(\d+),\s*(.*)$')
_VECTOR = re.compile(r"^\+?C4<([01xz]*)>;\s*$")
_OTHER_KINDS = frozenset({"str", "real"})  # a string or a real number has no x or z bit
_WARNING = re.compile(r"^([^\n:]+):(\d+): warning: (.*)$", re.M)
# The warnings of `-Wselect-range`, and the x index of an array, as Icarus 12 words them
_OUT_OF_RANGE = re.compile(
    r"out of bound|outside the vector|undefined array access|'bx|is selecting (?:before|after)"
    r"|select \[[^\]]*\] is (?:before|after)",
    re.I,
)


def files(vvp: str) -> list[str]:
    """The file table at the end of the `.vvp` text (a file index is a position in it)."""
    lines = vvp.splitlines()
    for at, line in enumerate(lines):
        table = _FILE_TABLE.match(line)
        if table is None:
            continue
        count = int(table.group(1))
        names = [_FILE_NAME.match(row) for row in lines[at + 1 : at + 1 + count]]
        if len(names) != count or None in names:
            raise IcarusError("the file table of the simulator's output is unreadable")
        return [name.group(1) for name in names if name is not None]
    raise IcarusError("the simulator's output has no file table")


def x_parameter(vvp: str, ours: list[str]) -> Finding | None:
    """The first parameter, localparam or genvar (by file order, line) of the files in `ours`
    whose value in the elaborated design has an x or z bit, else None. Every line that defines
    a parameter must be readable, and a vector value must be a `C4<...>` of 0, 1, x and z."""
    table = files(vvp)
    found: list[tuple[int, int, str, str, str]] = []
    for row in vvp.splitlines():
        if _PARAM_LINE.match(row) is None:
            continue
        match = _PARAM.match(row)
        if match is None:
            raise IcarusError("a parameter in the simulator's output is unreadable")
        kind, name, _, index, line, value = match.groups()
        if int(index) >= len(table):
            raise IcarusError(f"the parameter `{name}` names a file that is not in the table")
        file = table[int(index)]
        if kind in _OTHER_KINDS:
            continue
        vector = _VECTOR.match(value) if kind == "l" else None
        if vector is None:
            raise IcarusError(f"the value of the parameter `{name}` is unreadable")
        bits = vector.group(1)
        if file in ours and ("x" in bits or "z" in bits):
            shown = name.replace('\\"', '"')
            found.append((ours.index(file), int(line), shown, bits, file))
    if not found:
        return None
    _, line, name, bits, file = min(found)
    shown_bits = bits if len(bits) <= 32 else f"{bits[:32]}..."
    return Finding(
        file,
        line,
        f"the RTL ({file}, line {line}) gives the parameter `{name}` a value with an x or z bit "
        f"in a simulator (Icarus reads {len(bits)}'b{shown_bits}; such a value comes from "
        "`1/0`, `0 ** -1`, a select outside the range or a function that returns x): a "
        "simulator and the synthesis can choose generate branches and case items differently "
        "for it, so the check cannot vouch for the design. Give it a defined value",
    )


def x_select(output: str, ours: list[str]) -> Finding | None:
    """The first warning in the compiler's `output` (by file order, line) that a constant select
    or array index in one of the files in `ours` is outside the range, else None."""
    found: list[tuple[int, int, str, str]] = []
    for match in _WARNING.finditer(output):
        file, line, text = match.group(1), int(match.group(2)), match.group(3).strip()
        if file in ours and _OUT_OF_RANGE.search(text):
            found.append((ours.index(file), line, text, file))
    if not found:
        return None
    _, line, text, file = min(found)
    return Finding(
        file,
        line,
        f"the RTL ({file}, line {line}) selects outside the range of a vector, parameter or "
        f'array with a constant index (Icarus: "{text[:160]}"): a simulator reads x there and '
        "the gates some value, and a constant built this way can steer generate branches "
        "and case items differently in a simulator and in the synthesis, so the check cannot "
        "vouch for the design. Keep constant indexes inside the declared range",
    )
