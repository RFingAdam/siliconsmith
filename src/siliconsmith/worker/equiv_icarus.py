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
Three questions, answered from that text (no subprocess here, so the rules can be tested by
hand):

* `x_parameter`: a parameter, localparam or genvar of our files whose value has an x or z bit;
* `x_select`: a warning that a constant select or index of our files is outside the range (the
  simulator reads x there, Yosys reads x or a value of its own, and the gates some value);
* `disagreement`: a parameter or localparam whose defined values differ from the ones in
  Verilator's tree (`equiv_tree.parameter_values`), which the other checks judge, in some module
  instance or loop pass. A select outside the range inside a constant function warns nowhere
  and leaves no x bit: Icarus reads x there and an `if (!v[6])` turns it into a defined value,
  while Verilator and Yosys read 0;
* `shape_disagreement`: a parameter or localparam with the same values in both tools but
  another width or sign (`equiv_tree.parameter_shapes`). Icarus works out an untyped
  parameter's value without losing bits: `8'hff + 8'h00` is 255 everywhere, but 9 bits wide in
  Icarus and 8 in Verilator and Yosys, and a concatenation `{M, M}` or `$bits(M)` sees that;
* `uncompared`: a parameter whose value holds a call that can give a simulator x
  (`equiv_tree.valued_calls`) but whose value is not compared (a real, a string, or a parameter
  only one tool lists).

Fail closed: text this module cannot read raises `IcarusError`, which the caller turns into a
refusal.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import NamedTuple, TypeVar


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
# `S_0x... .scope module, "u0" "sub" 4 7, 4 14 0, S_0x...;`: a scope (module instance, generate
# block or loop pass, named block, function, package), its instance name, its type name, where
# it is, and the label of the scope that holds it (none for the top module or a package). The
# declarations after it (`.param` lines) belong to it; `.scope S_0x...;` returns to a scope.
_SCOPE_LINE = re.compile(r"^\S+\s+\.scope\b")
_SCOPE = re.compile(
    r'^(\S+)\s+\.scope\s+[\w.$]+,\s*"((?:[^"\\]|\\.)*)"\s+"(?:[^"\\]|\\.)*"\s+\d+\s+\d+'
    r"(?:,\s*\d+\s+\d+\s+\d+,\s*(\S+))?;\s*$"
)
_RECALL = re.compile(r"^\s+\.scope\s+(\S+);\s*$")
# `P_0x... .param/l "NAME" <local> <file> <line>, <value>;` (a real carries a comment after `;`)
_PARAM_LINE = re.compile(r"^\S+\s+\.param/")
_PARAM = re.compile(r'^\S+\s+\.param/(\w+)\s+"((?:[^"\\]|\\.)*)"\s+(\d+)\s+(\d+)\s+(\d+),\s*(.*)$')
_VECTOR = re.compile(r"^(\+?)C4<([01xz]*)>;\s*$")  # `+`: a signed value
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


# Where a value is: the instance names from the top module (or package) down, as the simulator
# names its scopes (`("tt_um_x", "core", "b[0]")`).
Path = tuple[str, ...]


class _Param(NamedTuple):
    file: str
    line: int
    name: str
    bits: str | None  # None for a string or a real
    signed: bool
    path: Path


def _scopes(rows: list[str]) -> dict[str, Path]:
    """`{label: path}` of every scope the `.vvp` text declares (a parent may come later)."""
    declared: dict[str, tuple[str, str | None]] = {}
    for row in rows:
        if _SCOPE_LINE.match(row) is None:
            continue
        scope = _SCOPE.match(row)
        if scope is None or scope.group(1) in declared:
            raise IcarusError("a scope in the simulator's output is unreadable")
        declared[scope.group(1)] = (_unescape(scope.group(2)), scope.group(3))
    paths: dict[str, Path] = {}
    for label in declared:
        chain: list[str] = []
        at: str | None = label
        while at is not None and at not in paths:
            if at not in declared or at in chain:
                raise IcarusError("a scope in the simulator's output has no readable parent")
            chain.append(at)
            at = declared[at][1]
        path = paths[at] if at is not None else ()
        for step in reversed(chain):
            path = (*path, declared[step][0])
            paths[step] = path
    return paths


def _parameters(vvp: str) -> Iterator[_Param]:
    """Every parameter, localparam and genvar line, once per module instance and loop pass, with
    the path of the scope it is in (the last scope declared, or returned to, before it). Every
    such line and every scope must be readable, and a vector value must be a `C4<...>` of 0, 1,
    x and z."""
    table = files(vvp)
    rows = vvp.splitlines()
    scopes = _scopes(rows)
    current: Path | None = None
    for row in rows:
        scope = _SCOPE.match(row) or _RECALL.match(row)
        if scope is not None:
            current = scopes.get(scope.group(1))  # None: a label never declared
            continue
        if _PARAM_LINE.match(row) is None:
            continue
        match = _PARAM.match(row)
        if match is None:
            raise IcarusError("a parameter in the simulator's output is unreadable")
        kind, name, _, index, line, value = match.groups()
        if int(index) >= len(table):
            raise IcarusError(f"the parameter `{name}` names a file that is not in the table")
        if current is None:
            raise IcarusError(f"the parameter `{name}` is outside every scope")
        shown = _unescape(name)
        if kind in _OTHER_KINDS:
            yield _Param(table[int(index)], int(line), shown, None, False, current)
            continue
        vector = _VECTOR.match(value) if kind == "l" else None
        if vector is None:
            raise IcarusError(f"the value of the parameter `{name}` is unreadable")
        signed, bits = vector.groups()
        yield _Param(table[int(index)], int(line), shown, bits, signed == "+", current)


def _unescape(name: str) -> str:
    return name.replace('\\"', '"')


def x_parameter(vvp: str, ours: list[str]) -> Finding | None:
    """The first parameter, localparam or genvar (by file order, line) of the files in `ours`
    whose value in the elaborated design has an x or z bit, else None. Every line that defines
    a parameter must be readable (`_parameters`)."""
    found: list[tuple[int, int, str, str, str]] = []
    for file, line, name, bits, _, _ in _parameters(vvp):
        if bits is not None and file in ours and ("x" in bits or "z" in bits):
            found.append((ours.index(file), line, name, bits, file))
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


# A parameter's declaration (`(file, line, name)`) and its values, one per module instance (or
# loop pass), as `(path, number)`: the number is the value with its sign (a signed value whose
# top bit is set is negative). None when some value is not a plain vector of 0 and 1 (a string,
# a real, an x or z bit): such a parameter is not compared.
Key = tuple[str, int, str]
Values = dict[Key, list[tuple[Path, int]] | None]
# The width in bits of such a value and whether it is signed (`Shapes`: as `Values`, with the
# shape in place of the number)
Shape = tuple[int, bool]
Shapes = dict[Key, list[tuple[Path, Shape]] | None]
_T = TypeVar("_T")


def _plain(vvp: str, ours: list[str]) -> dict[Key, list[tuple[Path, int, Shape]] | None]:
    """The value (as a number with its sign) and the shape of every parameter, localparam and
    genvar of the files in `ours` in the simulator's elaboration, once per module instance and
    loop pass (`_parameters`); None as in `Values`."""
    found: dict[Key, list[tuple[Path, int, Shape]] | None] = {}
    for file, line, name, bits, signed, path in _parameters(vvp):
        if file not in ours:
            continue
        key = (file, line, name)
        known = found.setdefault(key, [])
        if bits is None or bits.strip("01") or not bits or known is None:
            found[key] = None
        else:
            number = int(bits, 2)
            if signed and bits[0] == "1":
                number -= 1 << len(bits)
            known.append((path, number, (len(bits), signed)))
    return found


def parameter_values(vvp: str, ours: list[str]) -> Values:
    """The value of every parameter, localparam and genvar of the files in `ours` in the
    simulator's elaboration, once per module instance and loop pass (`_parameters`)."""
    return {
        k: None if v is None else [(p, n) for p, n, _ in v] for k, v in _plain(vvp, ours).items()
    }


def parameter_shapes(vvp: str, ours: list[str]) -> Shapes:
    """The shape (width in bits, signed) of every value that `parameter_values` gives, with the
    same paths: `+C4<...>` is signed, and the width is the number of its bits."""
    return {
        k: None if v is None else [(p, s) for p, _, s in v] for k, v in _plain(vvp, ours).items()
    }


def _shown(values: list[tuple[Path, int]]) -> str:
    listed = [str(v) for v in sorted(v for _, v in values)]
    return ", ".join(listed[:4]) + (", ..." if len(listed) > 4 else "")


def _where(paths: list[Path]) -> str:
    named = sorted({".".join(p) for p in paths})
    return ", ".join(f"`{p}`" for p in named[:2]) + (", ..." if len(named) > 2 else "")


def _paired(
    mine: list[tuple[Path, _T]], theirs: list[tuple[Path, _T]]
) -> list[tuple[Path, _T, _T]] | None:
    """The two tools' entries of one parameter paired by path, in path order, or None when the
    paths cannot be paired one to one (a path twice in one tool, or a path only one tool has)."""
    a, b = dict(mine), dict(theirs)
    if len(a) != len(mine) or len(b) != len(theirs) or a.keys() != b.keys():
        return None
    return [(p, a[p], b[p]) for p in sorted(a)]


def _differ(mine: list[tuple[Path, int]], theirs: list[tuple[Path, int]]) -> tuple[str, bool]:
    """How the two tools' values of one parameter differ ("" if they do not), and whether that
    is a different value (True) or instances the check cannot pair (False)."""
    pairs = _paired(mine, theirs)
    if pairs is not None:
        wrong = [(p, x, y) for p, x, y in pairs if x != y]
        if not wrong:
            return "", True
        at, x, y = wrong[0]
        return (
            f"the value {x} in a simulator (Icarus) but {y} in Verilator's elaboration "
            f"(in `{'.'.join(at)}`)",
            True,
        )
    values = {v for _, v in mine}
    if values == {v for _, v in theirs}:
        if len(values) == 1:
            return "", True  # every instance has the one value in both tools
        return (
            f"the values {_shown(mine)} in its instances and loop passes, and the check cannot "
            f"pair the simulator's (Icarus) instances ({_where([p for p, _ in mine])}) with "
            f"Verilator's ({_where([p for p, _ in theirs])}) to see that each one has the same "
            "value in both",
            False,
        )
    return (
        f"the value {_shown(mine)} in a simulator (Icarus) but {_shown(theirs)} in Verilator's "
        "elaboration",
        True,
    )


def disagreement(simulator: Values, verilator: Values, ours: list[str]) -> Finding | None:
    """The first parameter or localparam (by file order, line, name) whose values in the
    simulator's elaboration (`parameter_values`) differ from those in Verilator's tree
    (`equiv_tree.parameter_values`), else None.

    The values are compared in full, as numbers with their sign, instance by instance (Icarus
    can make an untyped localparam wider than Verilator does: the number is compared here, the
    width and sign by `shape_disagreement`, which runs next): each path from
    the top module (`tt_um_x.core.b[0]`, an instance, generate block or loop pass, named block
    or function) must have the same value in both tools. When the two tools' paths of a
    parameter cannot be paired one to one (a block without a name: Icarus calls it
    `$unm_blk_2`, Verilator `unnamedblk1`), the values still pass if every instance has the one
    same value in both tools, and are refused otherwise. A parameter only one tool lists (a
    genvar has no value in Verilator's tree) or that holds a string, a real or an x is not
    compared here (an x is `x_parameter`'s, a call that can give x in a real or a string
    `uncompared`'s, and a localparam inside a function, which Verilator leaves out when it only
    works the function out as a constant, `equiv_tree.uncompared_local`'s: it must then be one
    number as written). The other checks judge the design Verilator elaborated and the proof the
    one Yosys did; a simulator that uses another value for a parameter (an index, a generate
    branch, a case item) runs a different design."""
    found: list[tuple[int, int, str, str, str, bool]] = []
    for key in simulator.keys() & verilator.keys():
        mine, theirs = simulator[key], verilator[key]
        if not mine or not theirs or key[0] not in ours:
            continue
        what, valued = _differ(mine, theirs)
        if what:
            file, line, name = key
            found.append((ours.index(file), line, name, file, what, valued))
    if not found:
        return None
    _, line, name, file, what, valued = min(found, key=lambda f: f[:3])
    why = (
        "which the other checks judge (a select outside the range inside a constant function "
        "does this: the simulator reads x there, and an `if` or `!` can turn that x into a "
        "defined value; so does an untyped localparam that overflows, which the simulator "
        "makes wider): a simulator and the synthesis can then index, choose generate branches "
        "and case items differently, so the check cannot vouch for the design. Compute it so "
        "that every tool gives the same value: keep every select inside the declared range, "
        "and give a localparam that can overflow a type or a size (`localparam [31:0] M = 1 "
        "<< 31;`)"
        if valued
        else "so it cannot vouch for the design. Give every block that holds this parameter "
        "a name (`begin : name`)"
    )
    return Finding(
        file, line, f"the RTL ({file}, line {line}) gives the parameter `{name}` {what}, {why}"
    )


def _fits(mine: Shape, theirs: Shape) -> bool:
    """Whether the check accepts a value that the simulator holds with the shape `mine` and
    Verilator with `theirs`: the same width and sign, or the same sign and a width of 32 bits
    or more in Verilator that Icarus makes wider (its lossless unsized arithmetic: `W - 1` is
    33 bits there, `W * 2` 64; too common in taught code to refuse, and read the same wherever
    the value is used as a number, while a concatenation or `$bits` of it is not detected)."""
    return mine == theirs or (mine[1] == theirs[1] and mine[0] > theirs[0] >= 32)


def _misfit(
    mine: list[tuple[Path, Shape]], theirs: list[tuple[Path, Shape]]
) -> tuple[Shape, Shape, Path | None] | None:
    """The first pair of the two tools' shapes of one parameter that does not fit (`_fits`), and
    its path, else None. The shapes are paired by path as `_differ` pairs values; when the paths
    cannot be paired one to one, every shape of the simulator must fit every one of Verilator's
    (no path then)."""
    pairs = _paired(mine, theirs)
    if pairs is None:
        mine_set, theirs_set = sorted({s for _, s in mine}), sorted({s for _, s in theirs})
        return next(((x, y, None) for x in mine_set for y in theirs_set if not _fits(x, y)), None)
    return next(((x, y, p) for p, x, y in pairs if not _fits(x, y)), None)


def _bits(shape: Shape) -> str:
    width, signed = shape
    return f"{width} {'signed' if signed else 'unsigned'} bit{'' if width == 1 else 's'}"


def shape_disagreement(simulator: Shapes, verilator: Shapes, ours: list[str]) -> Finding | None:
    """The first parameter or localparam (by file order, line, name) whose values have another
    width or sign in the simulator's elaboration (`parameter_shapes`) than in Verilator's tree
    (`equiv_tree.parameter_shapes`), which Yosys and the gates follow, else None. Run after
    `disagreement`, so the numbers already agree; the parameters compared are the ones it
    compares (both tools list them, with plain numbers), paired the same way (`_misfit`).

    Icarus works out an untyped parameter's value without losing bits (without
    `-gstrict-expr-width`): `localparam M = 8'hff + 8'h00;` is 255 in every tool, but 9 bits
    wide in Icarus and 8 in Verilator and Yosys, so `{M, M}`, `$bits(M)`, `&M` or `~M` give a
    simulator other bits than the gates. A typed localparam whose range calls a function that
    selects outside a vector's range differs the same way (round 16 rangecall: Icarus reads x
    there, and an `if` turns it into another number). A pair passes when it has the same width
    and sign, or the same sign and a width in Verilator of 32 bits or more that Icarus makes
    wider (`W - 1`, `W * 2`, `1 << 4`: common in taught code, read the same wherever the value
    is used as a number; a concatenation or `$bits` of it is not detected). Anything else is
    refused: a width under 32 bits in Verilator that Icarus makes wider, another sign, or a
    narrower width in Icarus."""
    found: list[tuple[int, int, str, str, Shape, Shape, Path | None]] = []
    for key in simulator.keys() & verilator.keys():
        mine, theirs = simulator[key], verilator[key]
        if not mine or not theirs or key[0] not in ours:
            continue
        misfit = _misfit(mine, theirs)
        if misfit is not None:
            file, line, name = key
            found.append((ours.index(file), line, name, file, *misfit))
    if not found:
        return None
    _, line, name, file, a, b, path = min(found, key=lambda f: f[:3])
    where = f"in `{'.'.join(path)}`" if path is not None else "in one of its instances"
    return Finding(
        file,
        line,
        f"the RTL ({file}, line {line}) gives the parameter `{name}` the same value but "
        f"{_bits(a)} in a simulator (Icarus) and {_bits(b)} in Verilator's elaboration and the "
        f"gates ({where}): a concatenation, `$bits`, a reduction or `~` sees that, so the check "
        "cannot vouch for the design. Icarus works out an untyped parameter's value without "
        "losing bits, so it is wider there (`8'hff + 8'h00` has 9 bits, 8 in the gates), and a "
        "select outside the range in a function that a range calls can change a width too. "
        "Give it a type or a size (`localparam [7:0] M = 8'hff + 8'h00;`) and keep every "
        "select inside the declared range",
    )


def uncompared(
    simulator: Values, verilator: Values, keys: frozenset[Key], ours: list[str]
) -> Finding | None:
    """The first parameter of `keys` (by file order, line, name) whose value is not compared by
    `disagreement`, else None. `keys` are the parameters whose value holds a call that can give
    a simulator x (`equiv_tree.valued_calls`): such a call is only allowed there because its
    value is compared, so it must be a plain number in both tools' every instance. A parameter
    that neither tool instantiates is fine."""
    found: list[tuple[int, int, str, str]] = []
    for file, line, name in keys:
        if file not in ours:
            continue
        mine, theirs = simulator.get((file, line, name), []), verilator.get((file, line, name), [])
        if (mine and theirs) or (mine == [] and theirs == []):
            continue
        found.append((ours.index(file), line, name, file))
    if not found:
        return None
    _, line, name, file = min(found)
    return Finding(
        file,
        line,
        f"the RTL ({file}, line {line}) gives the parameter `{name}` a value with a call of a "
        "function that can give a simulator x (it reads a vector at a computed index or "
        "divides), and the check cannot compare that value between the simulator (Icarus) and "
        "Verilator (a real or a string, or a parameter only one tool lists), so it cannot "
        "vouch for the design. Compute the call in an integer localparam first and use that",
    )
