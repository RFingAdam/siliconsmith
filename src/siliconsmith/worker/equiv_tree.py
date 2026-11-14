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
  with the simulator's (`valued_calls` names the parameters whose value holds such a call).
"""

from __future__ import annotations

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


def _constant_calls(tree: Any) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Every call of one of the design's functions that can give a simulator an x for constant
    arguments, made with constant arguments, with the parameter VAR whose value holds it (for
    an override, the child's parameter, the PIN's `modVarp`), or None anywhere else (an
    override whose parameter does not resolve too). See `constant_call`."""
    if not isinstance(tree, dict):
        raise TreeError("the tree is not an object")
    nodes = list(_subtree(tree))
    variables = {str(n["addr"]): n for n in nodes if n.get("type") == "VAR" and "addr" in n}
    kinds = {addr: str(n.get("varType", "")) for addr, n in variables.items()}
    functions = {str(n["addr"]): n for n in nodes if n.get("type") == "FUNC" and "addr" in n}

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

    risky: dict[str, bool] = {}

    def x_function(addr: str) -> bool:
        if addr not in risky:
            risky[addr] = False  # a recursive call adds nothing new
            func = functions.get(addr)
            risky[addr] = func is None or any(x_source(n) for n in _subtree(func))
        return risky[addr]

    def x_source(node: dict[str, Any]) -> bool:
        kind = str(node.get("type", ""))
        if kind == "FUNCREF":
            return x_function(str(node.get("taskp")))
        if _x_operator(node) is not None:
            return True
        if _is_select(kind) and not written(node):
            return any(
                reads_a_variable(child)
                for key, value in node.items()
                if key not in _SELECT_SOURCE and isinstance(value, list)
                for child in value
                if isinstance(child, dict)
            )
        return False

    def parameter(addr: Any) -> dict[str, Any] | None:
        var = variables.get(str(addr))
        return var if var is not None and kinds[str(addr)] in _PARAMETER_VALUES else None

    stack: list[tuple[dict[str, Any], dict[str, Any] | None]] = [(tree, None)]
    while stack:
        node, owner = stack.pop()
        kind = node.get("type")
        if kind == "FUNCREF":
            arguments = [a for a in node.get("argsp") or [] if isinstance(a, dict)]
            if not any(reads_a_variable(a) for a in arguments) and x_function(
                str(node.get("taskp"))
            ):
                yield node, owner
        for key, value in node.items():
            items = value if isinstance(value, list) else [value]
            for item in (i for i in items if isinstance(i, dict)):
                under = owner
                if kind == "VAR" and key == "valuep" and parameter(node.get("addr")) is not None:
                    under = node
                elif kind == "CELL" and key == "paramsp":
                    under = parameter(item.get("modVarp")) if item.get("type") == "PIN" else None
                stack.append((item, under))


def constant_call(tree: Any, meta: Any, ours: list[str]) -> Unknown | None:
    """The first call (by file order, line, column) of one of the design's functions that can
    give a simulator an x for constant arguments, made with constant arguments anywhere but in
    the value of a parameter or localparam or in a parameter override, else None. `tree` is
    Verilator's tree BEFORE V3Param (the `linkinc` stage), where every call is still there as
    written.

    Constant arguments read no variable: literals, parameters, localparams and genvars only (a
    variable read only for its size, `$bits(x)`, an `ATTROF`, counts as constant), or none. A
    function can give x for them (`_x_function`) when its body, or a function it calls, reads a
    select whose index reads a variable (`v[k]`, `v[k +: 2]`, `m[k]`: outside the range it is x
    to a simulator) or has an operator that can give x (`a / b`, `a % b`, a `**`, as in
    `x_condition`). A simulator works such a call out itself, and an `if` or `!` turns that x
    into a defined 0 or 1 that Yosys and Verilator need not use (round 15: `pick(4'b1010, 6)`
    with `if (!v[k])` in an always block, a case label, a part-select bound, a declaration range
    (also a parameter's: only its value is compared), an `if` of a clocked block, another
    function); only a parameter's value is compared with the simulator's, so that is where such
    a call belongs. A function without such a select or operator (a mask, a shift, a sum:
    `low_mask(3'd5)`, `rot(8'h81, 3'd2)`) is fine anywhere.

    An unresolved name (`PARSEREF`, a `VARREF` to no `VAR`) counts as a variable: only a module
    that nothing instantiates is left unlinked (in the design Verilator stops with an error)."""
    files = _file_letters(meta)
    found: list[tuple[int, int, int, str, str]] = []
    for node, owner in _constant_calls(tree):
        if owner is None:
            file, line, col = _place(node, files)
            if file in ours:
                found.append((ours.index(file), line, col, str(node.get("name", "?")), file))
    if not found:
        return None
    _, line, _, name, file = min(found)
    return Unknown(
        name,
        file,
        line,
        f"the RTL ({file}, line {line}) calls the function `{name}` with constant arguments "
        "outside the value of a parameter or localparam, and the function reads a vector at a "
        "computed index or divides: outside the range (or by 0) that is x to a simulator, which "
        "an `if` or `!` inside the function can turn into a 0 or 1 that Yosys does not use. The "
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
    for _, owner in _constant_calls(tree):
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
