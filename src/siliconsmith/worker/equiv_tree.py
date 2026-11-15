# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Which functions of the RTL are pure, read from Verilator's resolved syntax tree (no Verilator
here: the tree is a dict, so the rules can be tested by hand).

A simulator's `always @*` does not re-run when a signal that is read only inside a called function
changes, while synthesis inlines the function; Verilator's ALWCOMBORDER does not look inside
function bodies either. A function is therefore only allowed if it is PURE: every variable it
names is declared inside it (an argument, a local, its own return value) or is a parameter,
localparam or genvar, and every function it calls is pure. A `task` keyword never gets here (the
token rules refuse it); Verilator also models a `function void` as a TASK node, which is judged
like any function (a write to a signal that is not its own is still refused). Scope cannot be
judged from tokens (a block label, a cast, a same-named localparam in another module), which is
why this reads the tree Verilator resolved: `verilator --json-only` writes
`obj_dir/V<top>.tree.json` (and `.tree.meta.json`, which maps the file letters in every node's
`loc` to file names). Nodes have an `addr`; a `VARREF` has `varp` (the `addr` of the `VAR` it
resolves to) and a `FUNCREF` has `taskp` (the `FUNC` it calls).

Fail closed: a name or call that does not resolve, a missing position and the like raise
`TreeError`, which the caller turns into `not_proven`. It judges what the nodes point at (`varp`,
`taskp`, hierarchical names, a list of hidden-state nodes), not a whitelist of node types: a node
of an unknown type that reaches state some other way is not noticed (nothing Yosys accepts did).

The same tree answers two more questions (`early_read`, `xz_parameter`), and the tree Verilator
dumps before it evaluates the parameters (`--dump-tree-json`, the `linkinc` stage) two others
(`x_condition`, `constant_call`):

* Can a combinational block read a variable before it writes it, on SOME path through the block?
  A simulator does not rerun the block for its own write; the gates do. Verilator's ALWCOMBORDER
  only looks at the text order, so `if (c) begin t = a; y = t; end else begin y = t; t = a; end`
  slips past it. `early_read` walks every statement with the set of bits that are surely written
  on EVERY path so far. Unlike the purity walk this one is a whitelist: a statement node it does
  not know raises `TreeError`.
* Does a parameter, localparam or genvar-derived constant have an x or z bit (`1/0`, `5 % 0`,
  `0 ** -1`)? A simulator then compares with `===` semantics in a generate `case`, the tools pick
  generate branches differently, and the other checks would judge a different design.
* Can the condition of a generate `if`, `case` or `for` be x (a `/` or `%` by a value that is not
  a plain nonzero number, a `**`, a function call)? V3Param takes the first branch for an x `if`
  condition and drops the condition, so the evaluated tree cannot show it.
* Is one of the design's functions called with constant arguments outside a parameter value
  (`constant_call`)? A simulator works it out itself, and only a parameter's value is compared
  with the simulator's (`valued_calls` names the parameters whose value holds such a call). A
  select provably inside its range and a divisor provably nonzero do not count (`_Ranges`).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, NamedTuple

PARAM_TYPES = frozenset({"LPARAM", "GPARAM", "GENVAR"})
# A name that was not resolved is never a local: refuse to guess.
UNRESOLVED = frozenset({"PARSEREF", "DOT", "UNLINKEDDOT"})
# System functions with hidden state (they do not depend on the arguments).
STATEFUL = frozenset(
    {
        "RAND", "URANDOMRANGE", "TIME", "TIMED", "TESTPLUSARGS", "VALUEPLUSARGS", "FOPEN",
        "FOPENMCD", "FCLOSE", "FEOF", "FGETC", "FGETS", "FSCANF", "SSCANF", "FREAD", "FSEEK",
        "FTELL", "FERROR", "FFLUSH", "FREWIND", "FUNGETC", "READMEM", "SYSTEMT", "SYSTEMF",
    }
)  # fmt: skip
_LOC = re.compile(r"^([^,]+),(\d+):(\d+)")


class TreeError(Exception):
    """The tree is not in a shape this walker understands."""


@dataclass(frozen=True)
class Impure:
    """The first impure function: where it is, what it is, why."""

    name: str
    file: str
    line: int
    sentence: str


class PlacedNode(NamedTuple):
    """A node with a position in one of the files, as `placed_nodes` yields it. `keyword` is the
    `always` keyword of an ALWAYS node, `name` and `dotted` those of a FUNCREF or TASKREF."""

    type: str
    keyword: str
    name: str
    dotted: str
    file: str
    line: int
    col: int


def _children(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for value in node.values():
        if isinstance(value, dict):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield item


def _subtree(root: dict[str, Any]) -> Iterator[dict[str, Any]]:
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(_children(node))


def _place(node: dict[str, Any], files: dict[str, str]) -> tuple[str, int, int]:
    """`(file, line, column)` of a node; a file letter that meta.json does not know is an error."""
    match = _LOC.match(str(node.get("loc", "")))
    if match is None or match.group(1) not in files:
        raise TreeError(f"a `{node.get('type')}` node has no readable position")
    return files[match.group(1)], int(match.group(2)), int(match.group(3))


def _order(node: dict[str, Any]) -> tuple[int, int]:
    match = _LOC.match(str(node.get("loc", "")))
    return (int(match.group(2)), int(match.group(3))) if match else (0, 0)


def _file_letters(meta: Any) -> dict[str, str]:
    try:
        return {str(k): str(v["filename"]) for k, v in meta["files"].items()}
    except (AttributeError, KeyError, TypeError) as exc:
        raise TreeError("the file table is unreadable") from exc


Place = tuple[str, int, int]

_READS = (
    "which is not one of its arguments: a simulator's always @* does not re-run when it "
    "changes; pass it in as an argument"
)
_WRITES = (
    "which is not a local of the function: a simulator's always @* does not see that write; "
    "return the value instead"
)


def _scan(
    func: dict[str, Any], nodes: dict[str, dict[str, Any]], files: dict[str, str]
) -> tuple[str | None, list[tuple[str, Place]]]:
    """`(why it is impure or None, [(callee addr, place of the call)])` for one FUNC or TASK,
    judged on every node of its subtree."""
    name = str(func.get("name", "?"))
    body = sorted(_subtree(func), key=_order)
    local = {str(n["addr"]) for n in body if n.get("type") == "VAR" and "addr" in n}
    why: str | None = None
    edges: list[tuple[str, Place]] = []

    def look_up(node: dict[str, Any], key: str) -> dict[str, Any]:
        found = nodes.get(str(node[key]))
        if found is None:
            raise TreeError(
                f"a `{node.get('type')}` node in `{name}` has a `{key}` not in the tree"
            )
        return found

    for n in body:
        kind = str(n.get("type"))
        if kind in UNRESOLVED:
            raise TreeError(f"an unresolved `{kind}` node inside `{name}`")
        needs_place = "varp" in n or "taskp" in n or "loc" in n
        here = f"(line {_place(n, files)[1]})" if needs_place else ""
        if why is None and (kind.endswith("XREF") or (kind == "FUNCREF" and n.get("dotted"))):
            why = f"uses a hierarchical name {here}"
        elif why is None and kind in STATEFUL:
            why = f"calls a system function with a hidden state {here}"
        if "varp" in n:
            var = look_up(n, "varp")
            if var.get("type") != "VAR":
                raise TreeError(f"a `{kind}` node inside `{name}` points at a `{var.get('type')}`")
            own = str(var.get("addr")) in local
            param = var.get("isParam") is True or var.get("varType") in PARAM_TYPES
            if why is None and not own and not param:
                verb, tail = ("writes", _WRITES) if n.get("access") == "WR" else ("reads", _READS)
                why = f"{verb} `{var.get('name')}` {here}, {tail}"
        if "taskp" in n:
            called = look_up(n, "taskp")
            if called.get("type") not in ("FUNC", "TASK"):
                raise TreeError(f"a `{kind}` node inside `{name}` calls a `{called.get('type')}`")
            edges.append((str(called["addr"]), _place(n, files)))
    return why, edges


def impure_function(tree: Any, meta: Any, ours: list[str]) -> Impure | None:
    """The first impure function (by file order in `ours`, then line and column) of the files
    named in `ours`, or None. Functions of other files (Verilator's own std package) are not
    judged. Raises `TreeError` if the tree cannot be read."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    files = _file_letters(meta)
    nodes: dict[str, dict[str, Any]] = {}
    for node in _subtree(tree):
        if isinstance(node.get("addr"), str):
            nodes[node["addr"]] = node

    judged: dict[str, tuple[str, Place]] = {}  # addr -> (name, place), only our files
    why: dict[str, str] = {}
    calls: dict[str, list[tuple[str, Place]]] = {}
    for addr, func in nodes.items():
        if func.get("type") not in ("FUNC", "TASK"):
            continue
        where = _place(func, files)
        if where[0] in ours:
            judged[addr] = (str(func.get("name", "?")), where)
            reason, calls[addr] = _scan(func, nodes, files)
            if reason is not None:
                why[addr] = reason

    changed = True
    while changed:  # a function that calls an impure (or unjudged) one is impure
        changed = False
        for addr, edges in calls.items():
            for callee, at in edges:
                if addr in why:
                    break
                if callee not in judged:
                    other = nodes[callee].get("name")
                    why[addr] = f"calls `{other}` (line {at[1]}), which the check cannot read"
                elif callee in why:
                    other = judged[callee][0]
                    since = f"which is not pure: `{other}` {why[callee]}"
                    why[addr] = f"calls `{other}` (line {at[1]}), {since}"
                else:
                    continue
                changed = True

    bad = [a for a in judged if a in why]
    if not bad:
        return None
    first = min(bad, key=lambda a: (ours.index(judged[a][1][0]), *judged[a][1][1:]))
    name, (file, line, _) = judged[first]
    name = re.sub(r"__V\w*$", "", name)  # Verilator's wrapper of a function with default values
    return Impure(name, file, line, f"the function `{name}` ({file}, line {line}) {why[first]}")


def placed_nodes(tree: Any, meta: Any) -> Iterator[PlacedNode]:
    """Every node whose `loc` is in a file that `meta` names (nodes of Verilator's own std package
    and nodes without a position are left out). Raises `TreeError` if the tree or the file table
    cannot be read."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    files = _file_letters(meta)
    for node in _subtree(tree):
        match = _LOC.match(str(node.get("loc", "")))
        if match is None or match.group(1) not in files:
            continue
        yield PlacedNode(
            str(node.get("type")),
            str(node.get("keyword", "")),
            str(node.get("name", "")),
            str(node.get("dotted", "")),
            files[match.group(1)],
            int(match.group(2)),
            int(match.group(3)),
        )


# -- x and z constants -------------------------------------------------------------------------

# A based constant as Verilator prints it (`32'bxxxx`, `4'b10z1`, `8'h1f`): hex digits never hold
# an x or z, so any x, z or ? after the base letter is an unknown bit.
_XZ_CONST = re.compile(r"'s?[bodhBODH][0-9a-fA-F_]*[xXzZ?]")
_X_CONST = re.compile(r"'s?[bodhBODH][0-9a-fA-F_]*[xX]")


@dataclass(frozen=True)
class Unknown:
    """A constant with an x or z bit: the name of the parameter (or "" for a bare constant)."""

    name: str
    file: str
    line: int
    sentence: str


def xz_parameter(tree: Any, meta: Any, ours: list[str]) -> Unknown | None:
    """The first parameter, localparam or genvar (by file order, line, column) of our files whose
    value in Verilator's tree has an x or z bit, else the first constant with an x bit anywhere in
    our files (a genvar-derived expression such as `g / 0` that Verilator folded), else None.

    The RTL itself cannot spell an x (the token rules refuse it, and a z only as a `casez`
    label), so such a value was computed: `1/0`, `5 % 0`, `0 ** -1`. Yosys and a simulator give x,
    Verilator folds some of these to other values, and a simulator matches an x generate `case`
    item with `===` where Yosys does not."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    files = _file_letters(meta)
    found: list[tuple[int, int, int, str, str]] = []
    for node in _subtree(tree):
        kind = node.get("type")
        param = kind == "VAR" and (
            node.get("isParam") is True or node.get("varType") in PARAM_TYPES
        )
        if param:
            values = [n for v in node.get("valuep") or [] for n in _subtree(v)]
            consts = [str(n.get("name", "")) for n in values if n.get("type") == "CONST"]
            if not any(_XZ_CONST.search(c) for c in consts):
                continue
            name = str(node.get("name", "?"))
        elif kind == "CONST" and _X_CONST.search(str(node.get("name", ""))):
            name = ""
        else:
            continue
        match = _LOC.match(str(node.get("loc", "")))
        if match is None or match.group(1) not in files or files[match.group(1)] not in ours:
            continue
        file = files[match.group(1)]
        found.append((ours.index(file), int(match.group(2)), int(match.group(3)), name, file))
    if not found:
        return None
    _, line, _, name, file = min(found, key=lambda f: (f[0], f[1], f[2], f[3] == ""))
    what = f"the parameter `{name}`" if name else "a constant"
    return Unknown(
        name,
        file,
        line,
        f"the RTL ({file}, line {line}) computes {what} with an x or z bit (such as `1/0`, "
        "`5 % 0` or `0 ** -1`): a simulator and the synthesis can choose generate branches "
        "and case items differently for it, so the check cannot vouch for the design. Give it "
        "a defined value",
    )


# -- the value of every parameter ----------------------------------------------------------------

# A parameter's declaration (`(file, line, name)`) and its values, one per module instance and
# loop pass, as `(path, number)` (as `equiv_icarus.Values`): the path is the instance names from
# the top module (or package) down, the number the value with its sign. None when some value is
# not a plain number.
ParamKey = tuple[str, int, str]
Path = tuple[str, ...]
ParamValues = dict[ParamKey, list[tuple[Path, int]] | None]
_PLAIN_CONST = re.compile(r"^(\d+)'(s?)([bodh])([0-9a-fA-F_]+)$")
# The nodes inside a module that a simulator makes a scope of, named as Verilator names them: a
# generate block or loop pass (`b[0]`), a named block, a function or task
_SCOPES = frozenset({"GENBLOCK", "BEGIN", "FUNC", "TASK"})
_MAX_INSTANCES = 100_000  # module instances per module: more is not a design for one tile


def _plain(node: dict[str, Any] | None, signed: bool | None = None) -> int | None:
    """The number of a CONST node that is a plain number (no x or z bit), else None; negative
    when its top bit is set and it is `signed` (None: when the constant says `'s`)."""
    if node is None or node.get("type") != "CONST":
        return None
    match = _PLAIN_CONST.match(str(node.get("name", "")))
    if match is None:
        return None
    width = int(match.group(1))
    value = int(match.group(4).replace("_", ""), _BASES[match.group(3)])
    if signed is None:
        signed = match.group(2) == "s"
    if signed and width > 0 and (value >> (width - 1)) & 1:
        value -= 1 << width
    return value


def _inside(module: dict[str, Any]) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every node of `module` with the names of the generate blocks, named blocks, functions and
    tasks around it, from the outside in (a block without a name adds none)."""
    stack: list[tuple[Path, dict[str, Any]]] = [((), child) for child in _children(module)]
    while stack:
        around, node = stack.pop()
        yield around, node
        name = str(node.get("name") or "")
        inner = (*around, name) if node.get("type") in _SCOPES and name else around
        stack.extend((inner, child) for child in _children(node))


def _array(cell: dict[str, Any]) -> list[str]:
    """The names a simulator gives the instances of a CELL: its name, or `name[i]` for every
    element of an instance array (`sub u [3:0] (...)`, which Verilator keeps as one CELL with a
    range). A range that is not two plain numbers raises `TreeError`."""
    name = str(cell.get("name", "?"))
    ranges = cell.get("rangep") or []
    if not ranges:
        return [name]
    if not isinstance(ranges, list) or len(ranges) != 1 or not isinstance(ranges[0], dict):
        raise TreeError(f"the instance array `{name}` has an unreadable range")
    bounds = [_plain(_only(ranges[0], side)) for side in ("leftp", "rightp")]
    if bounds[0] is None or bounds[1] is None:
        raise TreeError(f"the instance array `{name}` has a range that is not a plain number")
    low, high = min(bounds[0], bounds[1]), max(bounds[0], bounds[1])
    if high - low >= _MAX_INSTANCES:
        raise TreeError(f"the instance array `{name}` has more than {_MAX_INSTANCES} elements")
    return [f"{name}[{i}]" for i in range(low, high + 1)]


def _instances(tree: dict[str, Any]) -> dict[str, tuple[dict[str, Any], list[Path]]]:
    """`{addr: (module, paths)}` for every module and package of Verilator's elaborated tree:
    the path of every instance of the module, from the top module down, as a simulator names
    it (`("tt_um_x", "core", "b[0]", "u")`). Verilator makes one copy of a module per set of
    parameter values and shares it between the instances that use the same set, so the paths
    go through every level: the top module (`level` 1) and a package once (their own name),
    every other module once per instance (CELL, each element of an instance array) inside each
    instance of the module that holds it, after the blocks around that CELL. A module nothing
    instantiates has no path. A tree with modules must have exactly one top module."""
    modules = tree.get("modulesp")
    if not isinstance(modules, list) or not all(isinstance(m, dict) for m in modules):
        raise TreeError("the tree has no readable list of modules")
    tops = [m for m in modules if m.get("type") == "MODULE" and m.get("level") == 1]
    if len(tops) != 1 and any(m.get("type") == "MODULE" for m in modules):
        raise TreeError("the tree does not have exactly one top module (level 1)")
    by_addr = {str(m.get("addr")): m for m in modules}
    parents: dict[str, list[tuple[str, Path, list[str]]]] = {addr: [] for addr in by_addr}
    for addr, module in by_addr.items():
        for around, node in _inside(module):
            if node.get("type") == "CELL":
                child = str(node.get("modp"))
                if child not in parents:
                    raise TreeError(f"the instance `{node.get('name')}` names no module")
                parents[child].append((addr, around, _array(node)))
    paths: dict[str, list[Path]] = {}

    def instances(addr: str, seen: frozenset[str]) -> list[Path]:
        if addr in paths:
            return paths[addr]
        if addr in seen:
            raise TreeError("the modules instantiate each other in a loop")
        module = by_addr[addr]
        if module.get("type") == "PACKAGE" or (tops and module is tops[0]):
            got: list[Path] = [(str(module.get("name", "?")),)]
        else:
            got = [
                path + around + (name,)
                for parent, around, names in parents[addr]
                for path in instances(parent, seen | {addr})
                for name in names
            ]
        if len(got) > _MAX_INSTANCES:
            raise TreeError(f"a module has more than {_MAX_INSTANCES} instances")
        paths[addr] = got
        return got

    return {addr: (module, instances(addr, frozenset())) for addr, module in by_addr.items()}


def _signed(var: dict[str, Any], nodes: dict[str, dict[str, Any]]) -> bool:
    """Whether the data type of a VAR is signed (through typedefs)."""
    dtype = nodes.get(str(var.get("dtypep")))
    for _ in range(32):
        if dtype is None or dtype.get("signed") is True:
            return dtype is not None
        ref = dtype.get("refDTypep") if dtype.get("type") == "REFDTYPE" else None
        dtype = nodes.get(str(ref)) if ref is not None else None
    return False


def parameter_values(tree: Any, meta: Any, ours: list[str]) -> ParamValues:
    """The value of every parameter and localparam of our files in Verilator's elaborated tree,
    once per module instance and loop pass, with its path (`_instances`, then the blocks around
    it in the module); a genvar has no value there and is left out. A value that is not a plain
    number (a real, an array, a string kept as text) makes it None. Compared with the
    simulator's values by `equiv_icarus.disagreement`."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    files = _file_letters(meta)
    nodes = {str(n["addr"]): n for n in _subtree(tree) if "addr" in n}
    found: ParamValues = {}
    for module, paths in _instances(tree).values():
        for around, node in _inside(module):
            if node.get("type") != "VAR" or not (
                node.get("isParam") is True or node.get("varType") in ("LPARAM", "GPARAM")
            ):
                continue
            match = _LOC.match(str(node.get("loc", "")))
            if match is None or match.group(1) not in files or files[match.group(1)] not in ours:
                continue
            key = (files[match.group(1)], int(match.group(2)), str(node.get("name", "?")))
            known = found.setdefault(key, [])
            values = node.get("valuep") or []
            only = values[0] if isinstance(values, list) and len(values) == 1 else None
            number = _plain(only, _signed(node, nodes)) if isinstance(only, dict) else None
            if number is None or known is None:
                found[key] = None
                continue
            known.extend((path + around, number) for path in paths)
    return found


# -- generate conditions that can be x ----------------------------------------------------------

# Only DIV, MODDIV and POW appear in the tree before V3Param (Verilator 5.046): the signed forms
# are made later, by V3Width. They are kept so that a tree of a later stage is judged the same.
_DIVIDES = frozenset({"DIV", "DIVS", "MODDIV", "MODDIVS"})
_POWERS = frozenset({"POW", "POWSS", "POWSU", "POWUS"})
# Where a generate construct keeps the expressions that choose its branches and loop passes.
_GENERATE_CONDITIONS = {
    "GENIF": ("condp",),
    "GENCASE": ("exprp",),
    "GENCASEITEM": ("condsp",),
    "GENFOR": ("initsp", "condp", "incsp"),
}
# A number as Verilator prints it before V3Width: `?32?sh5` (unsized) or `4'ha` (sized).
_EARLY_CONST = re.compile(r"^(?:\?(\d+)\?|(\d+)')(s?)([bodh])([0-9a-fA-F_]+)$")


def _number(node: dict[str, Any] | None) -> tuple[int, bool] | None:
    """`(value, negative)` of a CONST node that is a plain number (no x or z bit), else None."""
    if node is None or node.get("type") != "CONST":
        return None
    match = _EARLY_CONST.match(str(node.get("name", "")))
    if match is None:
        return None
    width = int(match.group(1) or match.group(2))
    value = int(match.group(5).replace("_", ""), _BASES[match.group(4)])
    negative = bool(match.group(3)) and width > 0 and (value >> (width - 1)) & 1 == 1
    return value, negative


def _x_operator(node: dict[str, Any]) -> str | None:
    """What makes `node` able to give x for some values of its operands, or None: a `/` or `%`
    whose divisor is not a plain nonzero number, a `**` whose base is not a plain nonzero number
    and whose exponent is not a plain non-negative number (`0 ** -1`), or a function call."""
    kind = node.get("type")
    if kind in _DIVIDES:
        divisor = _number(_only(node, "rhsp"))
        if divisor is None or divisor[0] == 0:
            return "a `/` or `%` whose divisor is not a plain nonzero number"
    elif kind in _POWERS:
        base, exponent = _number(_only(node, "lhsp")), _number(_only(node, "rhsp"))
        if (base is None or base[0] == 0) and (exponent is None or exponent[1]):
            return (
                "a `**` whose base is not a plain nonzero number and whose exponent is not a "
                "plain number of 0 or more"
            )
    elif kind == "FUNCREF":
        return f"a call of the function `{node.get('name', '?')}`"
    return None


def x_condition(tree: Any, meta: Any, ours: list[str]) -> Unknown | None:
    """The first operator (by file order, line, column) that can give x inside the condition of
    a generate `if`, `case` (expression and item labels) or `for` (all three parts of the header)
    of our files, else None. `tree` must be Verilator's tree BEFORE V3Param (the `linkinc` stage
    of `--dump-tree-json`): V3Param evaluates these conditions and keeps only the chosen branch.

    A simulator and Yosys treat an x `if` condition as false, Verilator takes the first branch,
    and the three match an x `case` label differently: the other checks would judge a branch
    that the proof or a simulator does not use. Such an x comes from a `/` or `%` by 0, from
    `0 ** -1` or from a constant function, which is why these are refused unless the operand
    shows they are safe (`WIDTH % 2`, `2 ** N`); a value computed in a localparam is judged by
    its value instead (`xz_parameter`, and the simulator's own values in `equiv_icarus`), and an
    index outside the range by the simulator's warning (`equiv_icarus.x_select`)."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    files = _file_letters(meta)
    found: list[tuple[int, int, int, str, str]] = []
    for node in _subtree(tree):
        keys = _GENERATE_CONDITIONS.get(str(node.get("type")))
        if keys is None:
            continue
        for key in keys:
            roots = node.get(key) or []
            if not isinstance(roots, list):
                raise TreeError(f"a `{node.get('type')}` node has an unexpected `{key}`")
            for inner in (n for root in roots if isinstance(root, dict) for n in _subtree(root)):
                what = _x_operator(inner)
                if what is None:
                    continue
                file, line, col = _place(inner if "loc" in inner else node, files)
                if file in ours:
                    found.append((ours.index(file), line, col, what, file))
    if not found:
        return None
    _, line, _, what, file = min(found)
    return Unknown(
        "",
        file,
        line,
        f"the RTL ({file}, line {line}) has {what} in the condition of a generate `if`, "
        "`case` or `for`: a divisor of 0, `0 ** -1` or a function that returns x makes that "
        "condition x, and a simulator, Yosys and Verilator can choose different branches for "
        "it, so the check cannot vouch for the design. Compute the value in a localparam "
        "first (a localparam with an x or z bit is refused)",
    )


# -- a call with constant arguments outside a parameter value ----------------------------------

# Where a call may take constant arguments: the value of a parameter or localparam (`valuep`, not
# its type or range) and a parameter override of an instance (`#(.W(f(3)))`, a `defparam`: the
# child's parameter). Each value there is compared with the simulator's (`parameter_values`,
# `equiv_icarus.disagreement`), and one that is not compared is refused (`valued_calls`,
# `equiv_icarus.uncompared`).
_PARAMETER_VALUES = frozenset({"LPARAM", "GPARAM"})
# What a select keeps besides its index: the vector it selects from, and its size.
_SELECT_SOURCE = frozenset({"fromp", "attrp"})


def _is_select(kind: str) -> bool:
    """A bit, part or array select (`SELBIT`, `SELEXTRACT`, `SELPLUS`, `SELMINUS`, `ARRAYSEL`,
    ...): any node type that starts or ends with SEL counts (fail closed)."""
    return kind.startswith("SEL") or kind.endswith("SEL")


# -- exact value ranges inside the design's functions ---------------------------------------------


class _Val(NamedTuple):
    """What the values of an index or divisor expression inside one of the design's functions
    can be, exactly (`_Ranges.value`): every value lies in `[lo, hi]` for every value its
    operands can take (an operand that is x aside, see `_Ranges`). `width` and `signed` are
    those of the expression's context; `signed` None: not known (a parameter's value is taken
    as a number only). `plain`: no operand has a value below 0."""

    lo: int
    hi: int
    width: int
    signed: bool | None
    plain: bool


_ARITHMETIC = frozenset({"ADD", "SUB", "MUL", "MULS"})
_COMPARISONS = {
    "LT": "<", "LTS": "<", "LTE": "<=", "LTES": "<=", "GT": ">", "GTS": ">", "GTE": ">=",
    "GTES": ">=",
}  # fmt: skip
_MIRRORED = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}
_RANGE_TEXT = re.compile(r"^(-?\d+):(-?\d+)$")
_SIZED_KEYWORDS = frozenset(
    {"logic", "reg", "bit", "integer", "int", "shortint", "longint", "byte"}
)
_JUMPS = frozenset({"JUMPBLOCK", "JUMPGO", "JUMPLABEL"})


def _is_loop(node: dict[str, Any]) -> bool:
    return node.get("type") == "LOOP"


def _fits(lo: int, hi: int, width: int, signed: bool) -> bool:
    """True if every value in `[lo, hi]` is held exactly by a signed or unsigned `width`-bit
    number."""
    if signed:
        return -(1 << (width - 1)) <= lo and hi < 1 << (width - 1)
    return lo >= 0 and hi < 1 << width


class _Ranges:
    """Exact value ranges of the index and divisor expressions inside the design's functions, on
    Verilator's tree before V3Param (`_constant_calls` asks `in_range` and `nonzero`).

    An expression's operands are constants (a number without an x or z bit), parameters and
    localparams with their one value in every instance (`known`: the value Verilator and the
    simulator agree on, found by the caller), input arguments that the function never writes, the
    variable of a simple loop inside the loop's body, and bit selects or constant part-selects
    (`k[3]`, `k[1:0]`; not an indexed part-select such as `k[0 +: 2]`) of them inside their range
    (only of a plain vector or integer that is itself one of these operands). An argument takes
    every value of its declared type (its width and sign: `logic [3:0]` 0 to 15, `integer` -2**31
    to 2**31-1); the variable of a simple loop takes, in the loop's body, the values the loop
    gives it (`scan`). `+`, `-` and `*` are worked out on whole numbers, then
    checked against the expression's real width and sign (`value`). Anything else (another
    operator, a local outside a simple loop, an argument the function writes, a variable of another
    type, a genvar, a parameter with several values, and a select of any of these or of an array's
    element) has no range: the select or divide stays an x source (fail closed).

    Why this is sound. (1) An input argument that the function never writes holds the value the call
    passed, cut to the argument's declared type, or x when the call passed x (which `_x_argument`
    refuses). So its declared range bounds it. A local is not used outside a simple loop, not even
    through a constant select (`loc[1:0]`, the function's result `f[1:0]`): one read before it is
    written is x. A constant select of a plain vector or integer holds the selected bits, so it is 0
    to 2**n - 1 for n selected indices; an element of an array is as wide as its own type, not one
    bit per index, so a select of an array has no range. (2) A simple loop is, in the tree, the
    statement `i = C0` right before a LOOP whose first statement is its test (`i < C`, `i <= C`,
    `i > C`, `i >= C` or the same with `i` on the right, `C` a constant expression), whose last
    statement is `i = i + 1` or `i = i - 1` (`i++`, `i--`), and whose other statements (the body)
    neither write `i` (no VARREF of `i` that is written, which includes an output argument) nor jump
    (a JUMPGO, JUMPBLOCK or JUMPLABEL: a `continue`, a `break`, a `disable`). Then `i` is `C0` when
    the test first runs and changes by 1 only after the body, so each time the body runs `i` has a
    value of the sequence C0, C0 +- 1, ... for which the test held. The sequence is exact (no
    wrap-around) up to the first value that fails the test: `C0` lies in `i`'s declared range, and
    that first failing value (`C`, `C + 1`, `C`, `C - 1`) must also lie in it. The test compares as
    whole numbers when `i` and `C` are both signed, or when every value involved is 0 or more (an
    unsigned compare reads a negative number as a large one). So the body sees `[C0, C - 1]`,
    `[C0, C]`, `[C + 1, C0]` or `[C, C0]`. A loop of another shape (a `break`, a `continue`, an
    increment inside a `begin`, a step other than 1) is not simple: its variable has no range there
    (a local, or an argument it writes). (3) Verilog works `+`, `-` and `*` modulo 2**W, W the width
    of the expression's context (the widest operand), and an index expression is its own context
    (self-determined). With every operand held exactly (an unsigned context extends a negative
    operand with zeros, so then every operand must be 0 or more), the result modulo 2**W equals the
    whole-number result, and it is the same number when that lies in the context's range (`_fits`).
    A parameter's value is used as a number of its smallest width and an unknown sign, which can
    only make that range smaller than the real one. (4) A select at an index that is not x and lies
    inside the declared range of the vector or array reads a defined bit there, in a simulator and
    in Yosys; a divisor that is never 0 gives a defined quotient. So inside a function whose every
    such select and divide is proven, x can only come from somewhere else: an argument that is x
    (`_constant_calls` refuses a constant call whose arguments can be x, see `_x_argument`), a local
    read before it is written, a function that leaves its result unset (both not detected, see
    `constant_call`; neither can reach a proven index, whose operands are constants, parameters with
    one value, unwritten arguments, loop variables, which the loop's first statement sets, and
    constant selects of these), or a real that is infinite or NaN converted to an integer in the
    body (`v = R;` with `R = 1.0e999`, then `v[k]` at a proven index reads an x bit; round 19:
    refused from Yosys's warning, `equiv._yosys_real_x`; it cannot reach a proven index either).
    A call of a function whose selects are proven, made inside another function, counts as
    proven only when every argument has a proven range itself (`_constant_calls`), so a callee's
    argument is never x either. By induction over the order the
    function runs in, the first x cannot come from a proven select or divide, whose operands are
    then all defined."""

    def __init__(self, variables: dict[str, dict[str, Any]], known: dict[str, int]) -> None:
        self.variables = variables
        self.known = known  # addr of a parameter's VAR -> its one agreed value
        self.loops: dict[str, tuple[int, int]] = {}  # addr of a VARREF -> its loop's range
        self.written: set[str] = set()  # addr of every VAR a scanned function writes

    # declared types ---------------------------------------------------------------------------

    def _exact(self, node: dict[str, Any] | None) -> int | None:
        """The one value of a constant expression, else None."""
        got = self.value(node) if node is not None else None
        return got.lo if got is not None and got.lo == got.hi else None

    def _bounds(self, dtype: dict[str, Any]) -> tuple[int, int] | None:
        """`(left, right)` of a BASICDTYPE's packed range (`[0, 0]` for one bit), else None."""
        if dtype.get("type") != "BASICDTYPE" or dtype.get("keyword") not in _SIZED_KEYWORDS:
            return None
        rng = _only(dtype, "rangep")
        if rng is not None:
            left, right = self._exact(_only(rng, "leftp")), self._exact(_only(rng, "rightp"))
            return None if left is None or right is None else (left, right)
        text = _RANGE_TEXT.match(str(dtype.get("range", "0:0")))
        return (int(text.group(1)), int(text.group(2))) if text else None

    def var_type(self, var: dict[str, Any]) -> _Val | None:
        """Every value a function's variable of a plain vector or integer type can hold."""
        if var.get("isFuncLocal") is not True:
            return None
        dtype = _only(var, "childDTypep")
        bounds = self._bounds(dtype) if dtype is not None else None
        if dtype is None or bounds is None:
            return None
        width = abs(bounds[0] - bounds[1]) + 1
        if dtype.get("signed") is True:
            return _Val(-(1 << (width - 1)), (1 << (width - 1)) - 1, width, True, False)
        return _Val(0, (1 << width) - 1, width, False, True)

    def dims(self, vector: dict[str, Any] | None) -> tuple[int, int] | None:
        """The lowest and highest index a select of `vector` may use: the declared range of a
        variable, parameter or localparam (the first unpacked one of a one-dimensional array),
        or the packed range of one element of such an array; else None."""
        if vector is None:
            return None
        element = vector.get("type") == "SELBIT"
        ref = _only(vector, "fromp") if element else vector
        var = self.variables.get(str(ref.get("varp"))) if ref is not None else None
        if ref is None or ref.get("type") != "VARREF" or var is None:
            return None
        dtype = _only(var, "childDTypep")
        if dtype is not None and dtype.get("type") == "UNPACKARRAYDTYPE":
            inner = _only(dtype, "childDTypep")
            if element:
                bounds = self._bounds(inner) if inner is not None else None
            elif inner is not None and inner.get("type") == "BASICDTYPE":
                rng = _only(dtype, "rangep")
                left = self._exact(_only(rng, "leftp")) if rng is not None else None
                right = self._exact(_only(rng, "rightp")) if rng is not None else None
                bounds = None if left is None or right is None else (left, right)
            else:
                bounds = None
        else:
            bounds = self._bounds(dtype) if dtype is not None and not element else None
        return None if bounds is None else (min(bounds), max(bounds))

    # expressions ------------------------------------------------------------------------------

    def _leaf(self, node: dict[str, Any]) -> _Val | None:
        kind = node.get("type")
        if kind == "CONST":
            match = _EARLY_CONST.match(str(node.get("name", "")))
            if match is None:
                return None
            width = int(match.group(1) or match.group(2))
            number = int(match.group(5).replace("_", ""), _BASES[match.group(4)])
            signed = bool(match.group(3))
            if width < 1 or number >> width:
                return None
            if signed and number >> (width - 1):
                number -= 1 << width
            return _Val(number, number, width, signed, number >= 0)
        if kind == "VARREF":
            addr = str(node.get("varp"))
            var = self.variables.get(addr)
            if var is None:
                return None
            if var.get("varType") in _PARAMETER_VALUES:
                held = self.known.get(addr)
                if held is None:
                    return None
                if held < 0:  # only a signed parameter holds a negative value
                    return _Val(held, held, (-held - 1).bit_length() + 1, True, False)
                return _Val(held, held, max(held.bit_length(), 1), None, True)
            if var.get("varType") in PARAM_TYPES:  # a genvar
                return None
            declared = self.var_type(var)
            looped = self.loops.get(str(node.get("addr")))
            if declared is not None and looped is not None:
                return declared._replace(lo=looped[0], hi=looped[1], plain=looped[0] >= 0)
            if var.get("direction") != "INPUT" or addr in self.written:
                return None  # a local can be read before it is written: x, not its type
            return declared
        if kind in ("SELBIT", "SELEXTRACT"):  # a constant select inside the range: unsigned
            source = _only(node, "fromp")
            if source is None or source.get("type") != "VARREF":
                return None  # a select of a select, of an element, of an expression
            var = self.variables.get(str(source.get("varp")))
            dtype = _only(var, "childDTypep") if var is not None else None
            if dtype is None or dtype.get("type") != "BASICDTYPE" or self._leaf(source) is None:
                return None  # an array (its element is wider than one index), or a vector
                # that is not itself an operand: a local or the function's result (x before it
                # is written), an argument the function writes, an unagreed parameter
            span = self.dims(source)
            ends = (
                [self._exact(_only(node, "bitp"))]
                if kind == "SELBIT"
                else [self._exact(_only(node, "leftp")), self._exact(_only(node, "rightp"))]
            )
            if span is None or any(e is None or not span[0] <= e <= span[1] for e in ends):
                return None
            known = [e for e in ends if e is not None]
            width = max(known) - min(known) + 1
            return _Val(0, (1 << width) - 1, width, False, True)
        return None

    def value(self, node: dict[str, Any]) -> _Val | None:
        """The exact range of an expression of `+`, `-`, `*` over `_leaf`s, else None."""
        leaves: list[_Val] = []

        def walk(item: dict[str, Any]) -> tuple[int, int] | None:
            if item.get("type") in _ARITHMETIC:
                left, right = _only(item, "lhsp"), _only(item, "rhsp")
                a = walk(left) if left is not None else None
                b = walk(right) if right is not None else None
                if a is None or b is None:
                    return None
                if item["type"] == "ADD":
                    return a[0] + b[0], a[1] + b[1]
                if item["type"] == "SUB":
                    return a[0] - b[1], a[1] - b[0]
                corners = [x * y for x in a for y in b]
                return min(corners), max(corners)
            leaf = self._leaf(item)
            if leaf is None:
                return None
            leaves.append(leaf)
            return leaf.lo, leaf.hi

        got = walk(node)
        if got is None or not leaves:
            return None
        width = max(v.width for v in leaves)
        signs = {v.signed for v in leaves}
        signed = False if False in signs else (None if None in signs else True)
        plain = all(v.plain for v in leaves)
        if signed is not True and not plain:  # zero-extended: a negative operand changes
            return None
        if signed is None:  # a parameter of unknown sign: it fits either way (a signed one is
            # at least one bit wider than its value needs)
            wider = max(v.width + (1 if v.signed is None else 0) for v in leaves)
            fits = _fits(*got, width, False) and _fits(*got, wider, True)
        else:
            fits = _fits(*got, width, signed)
        return _Val(got[0], got[1], width, signed, plain) if fits else None

    # loops ------------------------------------------------------------------------------------

    def scan(self, func: dict[str, Any]) -> None:
        """Note the variables `func` writes, and, for every read of a simple loop's variable in
        its body, the values it takes there (see the class)."""
        for node in _subtree(func):
            if node.get("type") == "VARREF" and node.get("access") != "RD":
                self.written.add(str(node.get("varp")))
        for parent in _subtree(func):
            for items in parent.values():
                if not isinstance(items, list):
                    continue
                for before, loop in itertools.pairwise(items):
                    if isinstance(before, dict) and isinstance(loop, dict) and _is_loop(loop):
                        self._loop(before, loop)

    def _assigned(self, stmt: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """`(VAR addr, value)` of `x = value` to a plain variable, else None."""
        if stmt.get("type") != "ASSIGN":
            return None
        target, value = _only(stmt, "lhsp"), _only(stmt, "rhsp")
        if target is None or value is None or target.get("type") != "VARREF":
            return None
        return str(target.get("varp")), value

    def _step(self, stmt: dict[str, Any], addr: str) -> int | None:
        """+1 or -1 if `stmt` is `i = i + 1` (`1 + i`) or `i = i - 1` of the variable `addr`."""
        got = self._assigned(stmt)
        if got is None or got[0] != addr or got[1].get("type") not in ("ADD", "SUB"):
            return None
        left, right = _only(got[1], "lhsp"), _only(got[1], "rhsp")
        if left is None or right is None:
            return None

        def is_var(n: dict[str, Any]) -> bool:
            return n.get("type") == "VARREF" and str(n.get("varp")) == addr

        def one(n: dict[str, Any]) -> bool:
            leaf = self._leaf(n) if n.get("type") == "CONST" else None
            return leaf is not None and leaf.lo == 1

        if got[1]["type"] == "SUB":
            return -1 if is_var(left) and one(right) else None
        return 1 if (is_var(left) and one(right)) or (one(left) and is_var(right)) else None

    def _loop(self, init: dict[str, Any], loop: dict[str, Any]) -> None:
        stmts = loop.get("stmtsp") or []
        if len(stmts) < 2 or not all(isinstance(s, dict) for s in stmts):
            return
        test, body, last = stmts[0], stmts[1:-1], stmts[-1]
        start = self._assigned(init)
        if start is None or test.get("type") != "LOOPTEST":
            return
        addr = start[0]
        var = self.variables.get(addr)
        declared = self.var_type(var) if var is not None else None
        step = self._step(last, addr)
        c0 = self._exact(start[1])
        cond = _only(test, "condp")
        if declared is None or step is None or c0 is None or cond is None:
            return
        if not declared.lo <= c0 <= declared.hi:
            return
        op = _COMPARISONS.get(str(cond.get("type")))
        left, right = _only(cond, "lhsp"), _only(cond, "rhsp")
        if op is None or left is None or right is None:
            return
        if right.get("type") == "VARREF" and str(right.get("varp")) == addr:
            op, left, right = _MIRRORED[op], right, left
        if left.get("type") != "VARREF" or str(left.get("varp")) != addr:
            return
        bound = self.value(right)
        if bound is None or bound.lo != bound.hi:
            return
        c = bound.lo
        for node in (n for s in body for n in _subtree(s)):
            if node.get("type") in _JUMPS:
                return
            mine = node.get("type") == "VARREF" and str(node.get("varp")) == addr
            if mine and node.get("access") != "RD":
                return
        whole = declared.signed is True and bound.signed is True
        if step == 1 and op in ("<", "<="):
            stop = c if op == "<" else c + 1
            lo, hi = c0, stop - 1
            if stop > declared.hi or not (whole or (c0 >= 0 and bound.plain and c >= 0)):
                return
        elif step == -1 and op in (">", ">="):
            stop = c if op == ">" else c - 1
            lo, hi = stop + 1, c0
            if stop < declared.lo or not (whole or (stop >= 0 and bound.plain)):
                return
        else:
            return
        if lo > hi:
            return
        for node in (n for s in body for n in _subtree(s)):
            if node.get("type") == "VARREF" and str(node.get("varp")) == addr:
                self.loops[str(node.get("addr"))] = (lo, hi)

    # questions --------------------------------------------------------------------------------

    def in_range(self, select: dict[str, Any]) -> bool:
        """True if every index a read select can use lies inside the declared range of what it
        selects from: a bit select, or an indexed part select (`+:`, `-:`) of a constant width."""
        kind = select.get("type")
        span = self.dims(_only(select, "fromp"))
        index = _only(select, "bitp")
        got = self.value(index) if index is not None else None
        if span is None or got is None:
            return False
        if kind == "SELBIT":
            return span[0] <= got.lo and got.hi <= span[1]
        width = self._exact(_only(select, "widthp"))
        if width is None or width < 1:
            return False
        if kind == "SELPLUS":
            return span[0] <= got.lo and got.hi + width - 1 <= span[1]
        if kind == "SELMINUS":
            return span[0] <= got.lo - width + 1 and got.hi <= span[1]
        return False

    def nonzero(self, divide: dict[str, Any]) -> bool:
        """True if the divisor of a `/` or `%` is 1 or more for every value of its operands (all
        of them 0 or more, so that no context can change the number)."""
        divisor = _only(divide, "rhsp")
        got = self.value(divisor) if divisor is not None else None
        return got is not None and got.plain and got.lo >= 1


# Integer expression nodes whose type ends in D, which otherwise marks a real operator (`_real`):
# `+`, `&`, `?:`, `&&`, `&x`, `$signed`, `$unsigned`, a width extension, `==?`, `!=?`,
# `$isunbounded` and an unpacked-to-packed conversion. Any other type ending in D counts as real
# (fail closed: a constant call with it is refused when its function's selects were proven).
_INTEGER_D = frozenset(
    {
        "ADD", "AND", "COND", "LOGAND", "REDAND", "SIGNED", "UNSIGNED", "EXTEND", "EQWILD",
        "NEQWILD", "ISUNBOUNDED", "CVTARRAYTOPACKED",
    }
)  # fmt: skip


def _real(kind: str) -> bool:
    """True if a node of this type makes or converts a real (Verilator 5.046): its real
    operators end in D (`$sqrt` is SQRTD, `$ln` LOGD, `$bitstoreal` BITSTOREALD, `$itor` ITORD,
    `$realtime` TIMED; any type ending in D that is not one of `_INTEGER_D` counts, fail
    closed), `$rtoi` is RTOIS, `$realtobits` REALTOBITS, and every cast is a CAST... node
    (`integer'(R)`, `8'(R)`)."""
    return (
        kind.startswith(("CAST", "RTOI"))
        or "REAL" in kind
        or (kind.endswith("D") and kind not in _INTEGER_D)
    )


def _holds_bits(var: dict[str, Any]) -> bool:
    """True if a parameter's VAR is declared with an integer type or a packed range
    (`localparam [39:0] MSG = "HELLO"`, `parameter integer N`, `localparam logic [7:0] K`: one
    BASICDTYPE whose keyword is `_SIZED_KEYWORDS`; an untyped parameter is LOGIC_IMPLICIT, also
    with only `signed`, a real is `real`, a typedef a REFDTYPE). Such a parameter holds bits and
    never a real: a real set into it is converted where it is declared or overridden, and an
    infinite or NaN one there is refused (Yosys warns `converting real value inf to binary
    8'x` at that line, `equiv._yosys_real_x`, and a simulator makes the parameter all x,
    `equiv_icarus.x_parameter`)."""
    types = var.get("childDTypep") or []
    return (
        len(types) == 1
        and isinstance(types[0], dict)
        and types[0].get("type") == "BASICDTYPE"
        and types[0].get("keyword") in _SIZED_KEYWORDS
    )


def _real_argument(call: dict[str, Any], unreal: frozenset[str], parseref: bool = True) -> bool:
    """True if an argument of a call can hold a real: a real literal (a CONST whose name is
    neither a number, `?32?sh5` or `4'ha`, nor a fill such as `'1`, nor a string: `inf.0`,
    `2.5`), a parameter or localparam whose `addr` is in `unreal` (its value is not a plain
    number in every instance in both tools, and it is not declared to hold bits, `_holds_bits`:
    a real, typed or untyped, also one an override sets; an untyped string or another value
    that is not a plain number counts too, fail closed), a hierarchical name or one not yet
    resolved (a VARXREF, any type ending in XREF, a DOT: `g.R` of a generate block; before
    V3Param it points at no VAR whose value could be judged, and `reads_a_variable` does not
    count it; a PARSEREF only if `parseref`: in a call that reads a variable it is an unlinked
    module's plain name), or a node that makes a real (`_real`: `$sqrt(-1)`, `$ln(0)`,
    `$bitstoreal(...)`; not a cast, whatever a cast converts is judged by itself). An argument
    that also reads a variable counts too (`R + i`, `R + a`).

    Round 19: Yosys works a call with constant arguments out itself and converts a real
    argument without a warning (`equiv._yosys_real_x` does not see it), while a simulator
    converts an infinite or NaN real to an all-x integer. Even a function without a computed
    index gives that x back (`f = a + 1`), and a case label that is x matches nothing in a
    simulator while the gates need not keep the x (`case (ui_in) f(R): ...` with
    `R = 1.0e999`), so this counts for a call of any function (`f(g.R)` with `localparam real
    R = 1.0e999` in the generate block `g`, too). Round 20: Yosys also works out, silently, a
    call in a parameter value (`localparam [7:0] K = g(R)`, where Verilator and a simulator
    agree on another value, so the comparison of the values does not see it), a call whose
    argument reads a loop variable once it unrolls the loop (`f(R + i)`) and one inside a
    function it works out (`h = f(R + a)`), so this counts for every call."""
    for arg in call.get("argsp") or []:
        for node in _subtree(arg) if isinstance(arg, dict) else ():
            kind = str(node.get("type", ""))
            if kind == "CONST" and not any(c in str(node.get("name", "")) for c in "'?\""):
                return True
            if kind == "VARREF" and str(node.get("varp")) in unreal:
                return True
            if kind.endswith("XREF") or (kind in UNRESOLVED and (parseref or kind != "PARSEREF")):
                return True
            if _real(kind) and not kind.startswith("CAST"):
                return True
    return False


def _x_argument(call: dict[str, Any], unplain: frozenset[str]) -> bool:
    """True if an argument of a call can be x for a simulator although it reads no variable:
    one that can hold a real (`_real_argument`), a constant that is not a plain number (an x
    or z bit; a fill such as `'1`; a string), a cast (`_real`: `integer'(R)`, `8'(R)`), or an
    operator that can give x (`_x_operator`: a divide, a `**`, another call). A real that is
    infinite or NaN (`1.0e999`, `1.0e999 - 1.0e999`, `$sqrt(-1)`) becomes an all-x integer in
    a simulator when it is converted to an integer (round 18: `f(integer'(R))` with
    `R = 1.0e999`), so a real counts whatever its value. Every parameter in `unplain` counts
    here, also one declared to hold bits (a string such as `localparam [7:0] CH = "A"`)."""
    if _real_argument(call, unplain):
        return True
    for arg in call.get("argsp") or []:
        for node in _subtree(arg) if isinstance(arg, dict) else ():
            kind = str(node.get("type", ""))
            if kind == "CONST" and _number(node) is None:
                return True
            if _real(kind) or _x_operator(node) is not None:
                return True
    return False


def _constant_calls(
    tree: Any, known: dict[str, int] | None = None, plain: frozenset[str] = frozenset()
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None, bool]]:
    """Every call of one of the design's functions that can give a simulator an x for constant
    arguments, made with constant arguments, with the parameter VAR whose value holds it (for
    an override, the child's parameter, the PIN's `modVarp`), or None anywhere else (an
    override whose parameter does not resolve too), and False. Then, with None and True, every
    call anywhere (also in a parameter value, and one whose arguments read a variable) with an
    argument that can hold a real (`_real_argument`), once. `known`: the one agreed value of a
    parameter, by the `addr` of its VAR (`_Ranges`). `plain`: the `addr`s of the parameters
    whose value is a plain number in every instance in both tools; any other parameter can make
    an argument x (`_x_argument`), or hold a real unless it is declared to hold bits
    (`_holds_bits`). See `constant_call`."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    nodes = list(_subtree(tree))
    variables = {str(n["addr"]): n for n in nodes if n.get("type") == "VAR" and "addr" in n}
    kinds = {addr: str(n.get("varType", "")) for addr, n in variables.items()}
    functions = {str(n["addr"]): n for n in nodes if n.get("type") == "FUNC" and "addr" in n}
    ranges = _Ranges(variables, known or {})
    unplain = frozenset(a for a, k in kinds.items() if k in _PARAMETER_VALUES and a not in plain)
    unreal = frozenset(a for a in unplain if not _holds_bits(variables[a]))

    def reads_a_variable(root: dict[str, Any]) -> bool:
        stack = [root]
        while stack:
            node = stack.pop()
            kind = node.get("type")
            if kind == "ATTROF":
                continue  # the size of a variable, not its value
            if kind == "PARSEREF" or (
                kind == "VARREF" and kinds.get(str(node.get("varp"))) not in PARAM_TYPES
            ):
                return True
            stack.extend(_children(node))
        return False

    def written(select: dict[str, Any]) -> bool:
        """True if the select is the target of a write (its vector is a VARREF written)."""
        base: dict[str, Any] | None = select
        while base is not None and _is_select(str(base.get("type", ""))):
            base = _only(base, "fromp")
        return base is not None and base.get("type") == "VARREF" and base.get("access") == "WR"

    # How a function can give x: SAFE never (for arguments without x), PROVEN only if an
    # argument is x (its every computed select and divide is proven by `ranges`), RISKY else.
    safe, proven, risky = 0, 1, 2

    def defined(call: dict[str, Any]) -> bool:
        """True if every argument of a call inside a function has a proven range, so is never
        x (`_Ranges.value`: built from constants, parameters with one value, unwritten input
        arguments and loop variables)."""
        args = [a for a in call.get("argsp") or [] if isinstance(a, dict)]
        exprs = [_only(a, "exprp") for a in args]
        return all(e is not None and ranges.value(e) is not None for e in exprs)

    def x_source(node: dict[str, Any]) -> int:
        """How a node other than a call can give x (a call: `through`)."""
        kind = str(node.get("type", ""))
        if _x_operator(node) is not None:
            return proven if kind in _DIVIDES and ranges.nonzero(node) else risky
        if _is_select(kind) and not written(node):
            computed = any(
                reads_a_variable(child)
                for key, value in node.items()
                if key not in _SELECT_SOURCE and isinstance(value, list)
                for child in value
                if isinstance(child, dict)
            )
            if not computed:
                return safe
            return proven if ranges.in_range(node) else risky
        return safe

    # Every function's level at once, the least fixpoint over the calls between them (a call
    # adds its callee's level, RISKY for a PROVEN callee unless `defined`): from each function's
    # own nodes, raised until no call raises one more. Levels only rise and there are three, so
    # it ends. Round 18: judged one by one as the calls were met, a function called back (`f`
    # calls `g` calls `f`) was read as SAFE while it was still being judged, and kept that.
    for func in functions.values():
        ranges.scan(func)  # the loops: `defined` and `in_range` read those of their own function
    level: dict[str, int] = {}
    calls: dict[str, list[tuple[str, bool]]] = {}
    for addr, func in functions.items():
        inside = list(_subtree(func))
        level[addr] = max((x_source(n) for n in inside if n.get("type") != "FUNCREF"), default=safe)
        calls[addr] = [
            (str(n.get("taskp")), defined(n)) for n in inside if n.get("type") == "FUNCREF"
        ]

    def through(callee: str, ok: bool) -> int:
        how = level.get(callee, risky)  # a call of no FUNC the tree holds: RISKY
        return risky if how == proven and not ok else how

    rising = True
    while rising:
        rising = False
        for addr, made in calls.items():
            got = max([level[addr], *(through(callee, ok) for callee, ok in made)])
            if got > level[addr]:
                level[addr], rising = got, True

    def parameter(addr: Any) -> dict[str, Any] | None:
        var = variables.get(str(addr))
        return var if var is not None and kinds[str(addr)] in _PARAMETER_VALUES else None

    stack: list[tuple[dict[str, Any], dict[str, Any] | None]] = [(tree, None)]
    while stack:
        node, owner = stack.pop()
        kind = node.get("type")
        if kind == "FUNCREF":
            arguments = [a for a in node.get("argsp") or [] if isinstance(a, dict)]
            constant = not any(reads_a_variable(a) for a in arguments)
            how = level.get(str(node.get("taskp")), risky)
            x_call = constant and (how == risky or (how == proven and _x_argument(node, unplain)))
            # round 20: Yosys converts a real argument silently wherever it works a call out (a
            # parameter value, an unrolled loop, a function it works out), so it counts anywhere
            if _real_argument(node, unreal, parseref=constant):
                yield node, None, True
            if x_call:
                yield node, owner, False
        for key, value in node.items():
            items = value if isinstance(value, list) else [value]
            for item in (i for i in items if isinstance(i, dict)):
                under = owner
                if kind == "VAR" and key == "valuep" and parameter(node.get("addr")) is not None:
                    under = node
                elif kind == "CELL" and key == "paramsp":
                    under = parameter(item.get("modVarp")) if item.get("type") == "PIN" else None
                stack.append((item, under))


def _keys(tree: Any, files: dict[str, str]) -> dict[str, ParamKey]:
    """The key of each parameter's VAR in `tree` (as `parameter_values` keys them), by its
    `addr`."""
    found: dict[str, ParamKey] = {}
    for node in _subtree(tree) if isinstance(tree, dict) else ():
        if node.get("type") != "VAR" or node.get("varType") not in _PARAMETER_VALUES:
            continue
        match = _LOC.match(str(node.get("loc", "")))
        if match is None or match.group(1) not in files or "addr" not in node:
            continue
        found[str(node["addr"])] = (
            files[match.group(1)],
            int(match.group(2)),
            str(node.get("name", "?")),
        )
    return found


def _function_locals(tree: Any) -> frozenset[str]:
    """The `addr` of every VAR declared inside a function or task of `tree` (a FUNC or TASK
    node)."""
    found: set[str] = set()
    for node in _subtree(tree) if isinstance(tree, dict) else ():
        if node.get("type") in ("FUNC", "TASK"):
            found.update(
                str(n["addr"]) for n in _subtree(node) if n.get("type") == "VAR" and "addr" in n
            )
    return frozenset(found)


def agreed_values(verilator: ParamValues, simulator: ParamValues) -> dict[ParamKey, int]:
    """The parameters that have ONE value in every instance and loop pass, the same in
    Verilator's tree (`parameter_values`) and in the simulator's elaboration
    (`equiv_icarus.parameter_values`), with that value."""
    found: dict[ParamKey, int] = {}
    for key, mine in verilator.items():
        theirs = simulator.get(key)
        if not mine or not theirs:
            continue
        values = {v for _, v in mine} | {v for _, v in theirs}
        if len(values) == 1:
            found[key] = values.pop()
    return found


def plain_values(verilator: ParamValues, simulator: ParamValues) -> frozenset[ParamKey]:
    """The parameters whose value is a plain number (no real, string, x or z) in every instance
    and loop pass, both in Verilator's tree (`parameter_values`) and in the simulator's
    elaboration (`equiv_icarus.parameter_values`), and that both list with at least one value.
    Any other parameter can make an argument of a constant call x (`_x_argument`): a real that
    is infinite or NaN, typed or untyped, also one that an override sets."""
    return frozenset(key for key, mine in verilator.items() if mine and simulator.get(key))


def built_values(verilator: ParamValues, simulator: ParamValues) -> frozenset[ParamKey]:
    """The parameters that some tool builds: Verilator's tree has it in at least one module
    instance or loop pass, or with a value that is not a plain number (None: counted as built,
    fail closed), or the simulator's elaboration lists it at all. A parameter of our files that
    neither tool builds is in code that no tool runs (a generate branch the parameters do not
    choose, a loop with no pass, a module nothing instantiates), so a call that uses it is dead
    code in every tool (`constant_call`). Verilator's elaborated tree leaves out the local
    parameters of a function that it only works out for a parameter value, so only the
    simulator lists those: `constant_call` counts every parameter declared inside a function
    as built (round 20)."""
    return frozenset(key for key, mine in verilator.items() if mine is None or mine) | frozenset(
        simulator
    )


def constant_call(
    tree: Any,
    meta: Any,
    ours: list[str],
    agreed: dict[ParamKey, int] | None = None,
    plain: frozenset[ParamKey] = frozenset(),
    built: frozenset[ParamKey] | None = None,
) -> Unknown | None:
    """The first call (by file order, line, column) of one of the design's functions that can
    give a simulator an x for constant arguments, made with constant arguments anywhere but in
    the value of a parameter or localparam or in a parameter override, or with an argument that
    can hold a real anywhere (round 20, below), else None. `tree` is Verilator's tree BEFORE
    V3Param (the `linkinc` stage), where every call is still there as written.

    Constant arguments read no variable: literals, parameters, localparams and genvars only (a
    variable read only for its size, `$bits(x)`, an `ATTROF`, counts as constant), or none. A
    function can give x for them (its level in `_constant_calls`) when its body, or a function
    it calls, reads a select whose index reads a variable (`v[k]`, `v[k +: 2]`, `m[k]`: outside
    the range it is x to a simulator) or has an operator that can give x (`a / b`, `a % b`, a
    `**`, as in `x_condition`). A simulator works such a call out itself, and an `if` or `!`
    turns that x into a defined 0 or 1 that Yosys and Verilator need not use (round 15:
    `pick(4'b1010, 6)` with `if (!v[k])` in an always block, a case label, a part-select bound,
    a declaration range (also a parameter's: only its value is compared), an `if` of a clocked
    block, another function); only a parameter's value is compared with the simulator's, so that
    is where such a call belongs. A function without such a select or operator (a mask, a
    shift, a sum: `low_mask(3'd5)`, `rot(8'h81, 3'd2)`) is fine anywhere.

    Round 16 narrows this: a select whose index `_Ranges` proves inside the declared range for
    every value its operands can take, and a division whose divisor it proves nonzero, are no
    x source (`rev(8'h81)` with `for (i = 0; i < 8; i++) r[i] = v[7 - i];`, a 2-bit index into
    4 bits). Parameters count there with the one value in `agreed` (`agreed_values`: the same
    value in every instance in Verilator and in the simulator), never with a guess. Such a call
    still counts when one of its arguments can itself be x (`_x_argument`: an x or z literal, an
    operator that can give x, a real, a parameter that is not in `plain`, from `plain_values`),
    since the proof bounds only the defined values. The soundness argument is in `_Ranges`.

    Round 18: the level of each function is the least fixpoint over the calls between them, so
    that a call cycle (`f` calls `g` calls `f`) cannot hide a select, whatever order the calls
    come in; and a real argument (`f(integer'(R))`, `f(R)` with `R = 1.0e999`) counts as one
    that can be x: a simulator converts an infinite or NaN real to an all-x integer.

    Round 19: an argument that can hold a real (`_real_argument`: a real literal, a parameter
    that is not in `plain`, a hierarchical name such as `g.R`, a node that makes a real) counts
    for a call of ANY function outside a parameter value, also one without a computed index:
    Yosys works such a call out itself without a warning, and the all-x integer a simulator
    makes of an infinite real comes back out of `f = a + 1` too (a case label of x matches
    nothing in a simulator). And a parameter of our files that neither tool builds (not in
    `built`, from
    `built_values`: `localparam [7:0] M` in the generate branch `if (USE_REV)` with
    `USE_REV = 0`, a parameter of an optional sub-module that no branch instantiates) counts as
    plain: no tool runs the call that uses it. Which branches and instances the tools build is
    judged elsewhere (`x_condition`, the parameter values compared with the simulator's, and
    `equiv_elab.unmatched` for what Yosys builds). None (the default): every parameter that is
    not in `plain` can make an argument x. A hierarchical name in an argument (`f(g.R)`) always
    can (`_x_argument`).

    Round 20: an argument that can hold a real counts for EVERY call of one of the design's
    functions, wherever it is and whatever else its arguments read: in a parameter value or an
    override (`localparam [7:0] K = g(R)`: Yosys gives K another value than Verilator and a
    simulator, which agree, so the comparison of the values does not see it), in a call whose
    argument reads a loop variable (`f(R + i)`, worked out once Yosys unrolls the loop) and in a
    function that Yosys works out (`h = f(R + a)`). A parameter declared to hold bits
    (`_holds_bits`: `localparam [39:0] MSG = "HELLO"`) holds no real, so it does not count
    there, while an untyped or real one whose value is not a plain number does (a string
    without a packed range too, fail closed). A parameter declared inside a function counts
    as built whatever `built` says: Verilator's elaborated tree leaves out the local
    parameters of a function that it only works out for a parameter value, so only the
    simulator could list it, and a name that the two tools spelled differently would hide it.

    An unresolved name (`PARSEREF`, a `VARREF` to no `VAR`) counts as a variable: only a module
    that nothing instantiates is left unlinked (in the design Verilator stops with an error)."""
    files = _file_letters(meta)
    keys = _keys(tree, files)
    agreed = agreed or {}
    known = {addr: agreed[key] for addr, key in keys.items() if key in agreed}
    local = _function_locals(tree)
    plains = frozenset(
        addr
        for addr, key in keys.items()
        if key in plain
        or (built is not None and key[0] in ours and key not in built and addr not in local)
    )
    found: list[tuple[int, int, int, bool, str, str]] = []
    for node, owner, real in _constant_calls(tree, known, plains):
        if owner is None:
            file, line, col = _place(node, files)
            if file in ours:
                # at the same call, the constant-call sentence (it names a real too) comes first
                at = (ours.index(file), line, col, real, str(node.get("name", "?")), file)
                found.append(at)
    if not found:
        return None
    _, line, _, real, name, file = min(found)
    if real:
        return Unknown(
            name,
            file,
            line,
            f"the RTL ({file}, line {line}) calls the function `{name}` with an argument that "
            "can hold a real (a real literal or parameter, a function such as `$sqrt`, or a "
            "parameter or localparam without an integer type or a packed range whose value is "
            "not a plain number in both tools, such as an untyped string) or with a "
            "hierarchical name (`g.R`). Yosys works such a call out itself, also in a "
            "parameter value, an override, a loop or another function, and turns the real into "
            "a number without a warning, while a simulator converts an infinite or NaN real "
            "to an all-x integer, which any function can give back; a case label, an `if` or "
            "a `!` can turn that x into a 0 or 1 that the gates need not give, so the check "
            "cannot vouch for the design. Compute an integer localparam from the real first "
            "(`localparam integer N = R;`) and pass that, and give a string parameter a packed "
            'range (`localparam [7:0] CH = "A";`)',
        )
    return Unknown(
        name,
        file,
        line,
        f"the RTL ({file}, line {line}) calls the function `{name}` with constant arguments "
        "outside the value of a parameter or localparam, and the function reads a vector at a "
        "computed index that the check cannot prove inside the declared range or divides by a "
        "value it cannot prove nonzero, or an argument can be x (an x or z bit, a divide, "
        "another call, a cast, a parameter whose value is not a plain number in both tools "
        "such as a string, a hierarchical name, a real: a simulator converts an infinite or "
        "NaN real to an all-x integer, which any function can give back). A select outside "
        "the range or at an x index, a divide by 0 and such a real are x to a simulator, which "
        "an `if`, a `!` or a case label can turn into a 0 or 1 that Yosys does not use. The "
        "check compares such values only for parameters, so it cannot vouch for the design. "
        f"Compute it in a localparam first (`localparam integer V = {name}(...);`, inside the "
        "generate loop for a genvar argument) and use that",
    )


def valued_calls(tree: Any, meta: Any, ours: list[str]) -> frozenset[ParamKey]:
    """The parameters (`(file, line, name)`, as `parameter_values` keys them) whose value holds
    a call that `constant_call` allows only because the value is compared with the simulator's:
    `equiv_icarus.uncompared` refuses one whose value is not (a real, a string, a parameter
    only one tool lists). For an override, the child's parameter. `tree` is the `linkinc`
    tree."""
    files = _file_letters(meta)
    found: set[ParamKey] = set()
    for _, owner, _real in _constant_calls(tree):
        if owner is not None:
            file, line, _ = _place(owner, files)
            if file in ours:
                found.add((file, line, str(owner.get("name", "?"))))
    return frozenset(found)


# -- read before write in a combinational block -------------------------------------------------

ALL = -1  # every bit of a variable (a Python int with every bit set)
_CONST_VALUE = re.compile(r"^(\d+)'s?([bodh])([0-9a-fA-F_]+)$")
_BASES = {"b": 2, "o": 8, "d": 10, "h": 16}
_LEVEL = frozenset({"CHANGED", "COMBO"})
_EDGE = frozenset({"POS", "NEG", "BOTH"})
_SKIPPED_KEYWORDS = frozenset({"cont_assign", "always_ff", "always_latch"})
_BASIC_WIDTHS = frozenset({"logic", "bit", "reg", "integer", "int", "byte", "shortint", "longint"})
_READ_ONLY_STATEMENTS = frozenset({"DISPLAY", "STOP", "FINISH"})
_CASE_ENUMERATION_MAX = 12  # widest case expression whose items are counted to prove a full case


@dataclass(frozen=True)
class EarlyRead:
    """The first read of a variable that is not surely written yet on every path."""

    name: str
    file: str
    line: int
    sentence: str


def _const(node: dict[str, Any] | None) -> int | None:
    """The value of a CONST node without x or z bits, else None."""
    if node is None or node.get("type") != "CONST":
        return None
    match = _CONST_VALUE.match(str(node.get("name", "")))
    if match is None:
        return None
    return int(match.group(3).replace("_", ""), _BASES[match.group(2)])


def _only(node: dict[str, Any], key: str) -> dict[str, Any] | None:
    """The single child under `key` (None if there is none); several raise `TreeError`."""
    items = node.get(key) or []
    if not isinstance(items, list) or len(items) > 1:
        raise TreeError(f"a `{node.get('type')}` node has an unexpected `{key}`")
    return items[0] if items and isinstance(items[0], dict) else None


class _Walk:
    """The read-before-write walk of one combinational ALWAYS node (see `early_read`)."""

    def __init__(self, nodes: dict[str, dict[str, Any]], files: dict[str, str]) -> None:
        self.nodes, self.files = nodes, files
        self.written: set[str] = set()
        self.widths: dict[str, int | None] = {}
        self.jumps: dict[str, list[dict[str, int]]] = {}
        self.found: tuple[dict[str, Any], str] | None = None  # (VARREF, variable name)

    # widths -------------------------------------------------------------------------------

    def _dtype(self, addr: Any) -> dict[str, Any] | None:
        return self.nodes.get(str(addr)) if addr is not None else None

    def width(self, dtype: dict[str, Any] | None, depth: int = 0) -> int | None:
        """Bits of a data type, None when it cannot be told (then only a whole write counts)."""
        if dtype is None or depth > 32:
            return None
        kind = dtype.get("type")
        if kind == "BASICDTYPE":
            if dtype.get("keyword") not in _BASIC_WIDTHS:
                return None
            bounds = re.fullmatch(r"(-?\d+):(-?\d+)", str(dtype.get("range", "")))
            if bounds is None:
                return 1 if "range" not in dtype else None
            return abs(int(bounds.group(1)) - int(bounds.group(2))) + 1
        ref = dtype.get("refDTypep")
        inner = self._dtype(ref) if isinstance(ref, str) and ref.startswith("(") else None
        if inner is None:
            inner = _only(dtype, "childDTypep")
        if kind in ("REFDTYPE", "ENUMDTYPE", "MEMBERDTYPE"):
            return self.width(inner, depth + 1)
        if kind in ("PACKARRAYDTYPE", "UNPACKARRAYDTYPE"):
            bounds = re.fullmatch(r"\[(-?\d+):(-?\d+)\]", str(dtype.get("declRange", "")))
            each = self.width(inner, depth + 1)
            if bounds is None or each is None:
                return None
            return (abs(int(bounds.group(1)) - int(bounds.group(2))) + 1) * each
        if kind == "STRUCTDTYPE" and dtype.get("packed") is True:
            sizes = [self.width(m, depth + 1) for m in dtype.get("membersp") or []]
            return None if not sizes or None in sizes else sum(s for s in sizes if s is not None)
        return None

    def var(self, ref: dict[str, Any]) -> dict[str, Any]:
        found = self.nodes.get(str(ref.get("varp")))
        if found is None or found.get("type") != "VAR":
            raise TreeError(f"a `{ref.get('type')}` of `{ref.get('name')}` does not resolve")
        return found

    def full(self, var: dict[str, Any]) -> int:
        """The mask of every bit of a variable (ALL when its width cannot be told)."""
        addr = str(var["addr"])
        if addr not in self.widths:
            self.widths[addr] = self.width(self._dtype(var.get("dtypep")))
        bits = self.widths[addr]
        return ALL if bits is None else (1 << bits) - 1

    # where a select points ----------------------------------------------------------------

    def place(self, node: dict[str, Any]) -> tuple[dict[str, Any], int, int | None] | None:
        """`(VARREF, first bit, bits)` of a variable, a constant select or a constant array
        element of one (bits None: the whole variable); None for anything else."""
        kind = node.get("type")
        if kind in ("VARREF", "VARXREF"):
            return node, 0, None
        if kind == "SEL":
            inner = _only(node, "fromp")
            lsb = _const(_only(node, "lsbp"))
            width = node.get("widthConst")
            at = self.place(inner) if inner is not None else None
            if at is None or lsb is None or not isinstance(width, int):
                return None
            return at[0], at[1] + lsb, width
        if kind == "ARRAYSEL":
            inner = _only(node, "fromp")
            index = _const(_only(node, "bitp"))
            each = self.width(self._dtype(node.get("dtypep")))
            at = self.place(inner) if inner is not None else None
            if at is None or index is None or each is None:
                return None
            return at[0], at[1] + index * each, each
        return None

    def mask(self, ref: dict[str, Any], first: int, bits: int | None) -> int:
        var = self.var(ref)
        whole = self.full(var)
        if bits is None:
            return whole
        return ((1 << bits) - 1) << first if whole == ALL else (((1 << bits) - 1) << first) & whole

    # reads --------------------------------------------------------------------------------

    def read(self, node: dict[str, Any] | None, done: dict[str, int]) -> None:
        """Check every read in an expression (or a target's index) against `done`."""
        if node is None or self.found is not None:
            return
        kind = node.get("type")
        if kind in ("SEL", "ARRAYSEL"):
            at = self.place(node)
            if at is not None and at[0].get("access") in ("RD", "RW"):
                self.check(at[0], self.mask(at[0], at[1], at[2]), done)
                return
        if kind in ("VARREF", "VARXREF"):
            access = node.get("access")
            if access not in ("RD", "WR", "RW"):
                raise TreeError(f"a `{kind}` of `{node.get('name')}` has an unknown access")
            if access != "WR":
                self.check(node, self.mask(node, 0, None), done)
            return
        for child in _children(node):
            self.read(child, done)

    def check(self, ref: dict[str, Any], need: int, done: dict[str, int]) -> None:
        var = self.var(ref)  # a hierarchical read (VARXREF) too: `top.t` may be the block's `t`
        addr = str(var["addr"])
        if addr in self.written and self.found is None:
            have = done.get(addr, 0)
            whole = self.full(var)
            if have != ALL and (need & ~have) & (whole if whole != ALL else ALL) != 0:
                self.found = (ref, str(var.get("name", "?")))

    # writes -------------------------------------------------------------------------------

    def target(self, node: dict[str, Any] | None, done: dict[str, int]) -> dict[str, int]:
        """The bits a blocking write to `node` surely writes (a whole variable, a constant select
        or element, a concatenation of those); anything else writes nothing surely."""
        if node is None:
            return done
        if node.get("type") == "CONCAT":
            for side in ("lhsp", "rhsp"):
                done = self.target(_only(node, side), done)
            return done
        at = self.place(node)
        if at is None or at[0].get("type") != "VARREF":
            return done
        var = self.var(at[0])
        addr, whole = str(var["addr"]), self.full(var)
        bits = done.get(addr, 0) | self.mask(at[0], at[1], at[2])
        if whole != ALL and bits & whole == whole:
            bits = ALL
        return {**done, addr: bits}

    # statements ---------------------------------------------------------------------------

    def block(self, items: list[Any] | None, done: dict[str, int]) -> dict[str, int]:
        for item in items or []:
            if isinstance(item, dict):
                done = self.stmt(item, done)
        return done

    def stmt(self, node: dict[str, Any], done: dict[str, int]) -> dict[str, int]:
        kind = node.get("type")
        if kind == "BEGIN":
            for decl in node.get("declsp") or []:
                if decl.get("type") != "VAR":
                    raise TreeError(f"a `{decl.get('type')}` declared in a block")
            return self.block(node.get("stmtsp"), done)
        if kind == "VAR":
            return done
        if kind == "INITIALSTATICSTMT":  # a static local's start value: once, not per run
            for item in node.get("stmtsp") or []:
                self.read(_only(item, "rhsp"), done)
            return done
        if kind in ("ASSIGN", "ASSIGNDLY"):
            if node.get("timingControlp"):
                raise TreeError("an assignment with a timing control")
            rhs = _only(node, "rhsp")
            self.read(rhs, done)
            lhs = _only(node, "lhsp")
            self.read(lhs, done)
            return self.target(lhs, done) if kind == "ASSIGN" else done
        if kind == "IF":
            self.read(_only(node, "condp"), done)
            return _meet(self.block(node.get("thensp"), done), self.block(node.get("elsesp"), done))
        if kind == "CASE":
            return self.case(node, done)
        if kind == "LOOP":  # the body may run any number of times, even zero
            for key, value in node.items():
                if isinstance(value, list) and value and key not in ("stmtsp", "contsp"):
                    raise TreeError(f"a loop with an unexpected `{key}`")
            self.block(node.get("stmtsp"), done)
            self.block(node.get("contsp"), done)
            return done
        if kind == "LOOPTEST":
            self.read(_only(node, "condp"), done)
            return done
        if kind == "JUMPBLOCK":
            addr = str(node.get("addr"))
            self.jumps[addr] = []
            end = self.block(node.get("stmtsp"), done)
            for jumped in self.jumps.pop(addr):
                end = _meet(end, jumped)
            return end
        if kind == "JUMPGO":
            target = str(node.get("blockp"))
            if target not in self.jumps:
                raise TreeError("a jump out of a block the walk is not in")
            self.jumps[target].append(done)
            return done
        if kind == "STMTEXPR":  # a call's output argument never counts as surely written
            self.read(node, done)
            return done
        if kind in _READ_ONLY_STATEMENTS:
            self.read(node, done)
            return done
        raise TreeError(f"a `{kind}` statement in a combinational block")

    def case(self, node: dict[str, Any], done: dict[str, int]) -> dict[str, int]:
        if node.get("notParallelp"):
            raise TreeError("a `case` with a uniqueness check")
        self.read(_only(node, "exprp"), done)
        after: dict[str, int] | None = None
        labels: list[dict[str, Any]] = []
        default = False
        for item in node.get("itemsp") or []:
            conds = item.get("condsp") or []
            default = default or not conds
            labels += conds
            for cond in conds:
                self.read(cond, done)
            out = self.block(item.get("stmtsp"), done)
            after = out if after is None else _meet(after, out)
        if after is None:
            return done
        if not default and not self.full_case(node, labels):
            after = _meet(after, done)  # no item matches: nothing in the case ran
        return after

    def full_case(self, node: dict[str, Any], labels: list[dict[str, Any]]) -> bool:
        """True if constant labels cover every value of the case expression (a `casez` label's z
        and ? bits match anything). An x or z in the expression itself matches no label in a
        simulator, but it is an x in the RTL that Yosys reads, a don't-care of the proof."""
        expr = _only(node, "exprp")
        width = self.width(self._dtype(expr.get("dtypep"))) if expr is not None else None
        if width is None or width > _CASE_ENUMERATION_MAX:
            return False
        wild = node.get("kwd") == "casez"
        covered: set[int] = set()
        for label in labels:
            match = re.match(r"^(\d+)'s?([bh])([0-9a-fA-FzZ?_]+)$", str(label.get("name", "")))
            if label.get("type") != "CONST" or match is None or int(match.group(1)) != width:
                return False
            digits = match.group(3).replace("_", "")
            per = 1 if match.group(2) == "b" else 4
            bits = "".join(
                d * per if d in "zZ?" else format(int(d, 16), f"0{per}b") for d in digits
            )[-width:].rjust(width, "0")
            if not wild and any(b in "zZ?" for b in bits):
                continue  # a z label never matches in a plain `case`
            free = [i for i, b in enumerate(reversed(bits)) if b in "zZ?"]
            base = int("".join("0" if b in "zZ?" else b for b in bits), 2)
            for pick in range(1 << len(free)):
                value = base
                for k, i in enumerate(free):
                    if pick >> k & 1:
                        value |= 1 << i
                covered.add(value)
        return len(covered) == 1 << width


def _meet(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """What is surely written on both paths."""
    return {k: a[k] & b[k] for k in a.keys() & b.keys() if a[k] & b[k]}


def _combinational(node: dict[str, Any]) -> bool:
    """True for an `always_comb`, or an `always` with no edge in its sensitivity (`@*`, or only
    level items as MyHDL writes them); False for edge-triggered blocks, `always_latch` and
    continuous assignments. An unknown keyword or event raises `TreeError`."""
    keyword = node.get("keyword")
    if keyword in _SKIPPED_KEYWORDS:
        return False
    if keyword == "always_comb":
        return True
    if keyword != "always":
        raise TreeError(f"an always block with the keyword `{keyword}`")
    events = [
        str(item.get("edgeType"))
        for tree in node.get("sentreep") or []
        for item in tree.get("sensesp") or []
    ]
    unknown = [e for e in events if e not in _LEVEL | _EDGE]
    if unknown:
        raise TreeError(f"an always block sensitive to a `{unknown[0]}` event")
    return not any(e in _EDGE for e in events)


def early_read(tree: Any, meta: Any, ours: list[str]) -> EarlyRead | None:
    """The first read (by file order in `ours`, line, column) in a combinational block of our
    files of a variable that the block writes somewhere (blocking or not, also through an output
    argument) and that is not surely written yet on every path to the read; None if there is
    none. Raises `TreeError` if the tree cannot be read or holds a statement it does not know.
    A call's output argument never counts as surely written (`equiv_sv` refuses a function with
    an output, inout or ref argument before this runs, and every task).

    Along a path, the set D of surely written bits grows with every blocking assignment to a
    whole variable, a constant select or array element of one (bit by bit; the full width comes
    from the variable's data type) or a concatenation of those; a select with a variable index
    and a nonblocking write add nothing. An `if` keeps what both branches write (an `if` without
    `else` what was written before it); a `case` what every item writes, and when no item may
    match (no `default`, and the constant labels do not cover every value) what was written
    before it too: then nothing in the case ran, and a later read sees the value of the last
    run. A loop body is walked with D as it is before the loop and D after the loop is D before
    it (the body may run zero times, and a later pass may read what an earlier one did not write
    yet). A read must find all the bits it reads in D: a whole variable, or the bits of a
    constant select."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    files = _file_letters(meta)
    nodes = {str(n["addr"]): n for n in _subtree(tree) if isinstance(n.get("addr"), str)}
    found: list[tuple[int, int, int, str, str, int]] = []
    for node in _subtree(tree):
        if node.get("type") != "ALWAYS":
            continue
        file, line, _ = _place(node, files)
        if file not in ours or not _combinational(node):
            continue
        walk = _Walk(nodes, files)
        initial = {
            id(n) for s in _subtree(node) if s.get("type") == "INITIALSTATICSTMT"
            for n in _subtree(s)
        }  # fmt: skip
        for n in _subtree(node):
            if n.get("access") not in ("WR", "RW") or id(n) in initial:
                continue
            if n.get("type") != "VARREF":
                raise TreeError(f"a `{n.get('type')}` written in a combinational block")
            walk.written.add(str(walk.var(n)["addr"]))
        walk.block(node.get("stmtsp"), {})
        if walk.found is not None:
            ref, name = walk.found
            at = _place(ref, files)
            found.append((ours.index(file), at[1], at[2], name, at[0], line))
    if not found:
        return None
    _, line, _, name, file, _ = min(found)
    return EarlyRead(
        name,
        file,
        line,
        f"the RTL ({file}, line {line}) has a combinational block that can read `{name}` before "
        "it writes it on some path through the block (a branch, a case item or a loop pass that "
        "does not write it first): a simulator does not rerun the block for its own write, the "
        "gates do. Write each signal at the top of the block, before any `if`, `case` or loop",
    )
