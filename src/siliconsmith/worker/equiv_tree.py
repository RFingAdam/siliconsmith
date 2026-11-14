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

The same tree answers two more questions (`early_read`, `xz_parameter`):

* Can a combinational block read a variable before it writes it, on SOME path through the block?
  A simulator does not rerun the block for its own write; the gates do. Verilator's ALWCOMBORDER
  only looks at the text order, so `if (c) begin t = a; y = t; end else begin y = t; t = a; end`
  slips past it. `early_read` walks every statement with the set of bits that are surely written
  on EVERY path so far. Unlike the purity walk this one is a whitelist: a statement node it does
  not know raises `TreeError`.
* Does a parameter, localparam or genvar-derived constant have an x or z bit (`1/0`, `5 % 0`,
  `0 ** -1`)? A simulator then compares with `===` semantics in a generate `case`, the tools pick
  generate branches differently, and the other checks would judge a different design.
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
