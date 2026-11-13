# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0
"""Which functions of the RTL are pure, read from Verilator's resolved syntax tree (no Verilator
here: the tree is a dict, so the rules can be tested by hand).

A simulator's `always @*` does not re-run when a signal that is read only inside a called function
changes, while synthesis inlines the function; Verilator's ALWCOMBORDER does not look inside
function bodies either. A function is therefore only allowed if it is PURE: every variable it
names is declared inside it (an argument, a local, its own return value) or is a parameter,
localparam or genvar, and every function it calls is pure. A task is never allowed (it can write
any signal). Scope cannot be judged from tokens (a block label, a cast, a same-named localparam
in another module), which is why this reads the tree Verilator resolved:
`verilator --json-only` writes `obj_dir/V<top>.tree.json` (and `.tree.meta.json`, which maps the
file letters in every node's `loc` to file names). Nodes have an `addr`; a `VARREF` has `varp`
(the `addr` of the `VAR` it resolves to) and a `FUNCREF` has `taskp` (the `FUNC` it calls).

Fail closed: anything the walker does not understand raises `TreeError`, which the caller turns
into `not_proven`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

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
_TASK = (
    "can write any signal, and a simulator's always @* does not see it; use a "
    "`function automatic` that takes its inputs as arguments"
)


def _scan(
    func: dict[str, Any], nodes: dict[str, dict[str, Any]], files: dict[str, str]
) -> tuple[str | None, list[tuple[str, Place]]]:
    """`(why it is impure or None, [(callee addr, place of the call)])` for one FUNC or TASK,
    judged on every node of its subtree."""
    name = str(func.get("name", "?"))
    body = sorted(_subtree(func), key=_order)
    local = {str(n["addr"]) for n in body if n.get("type") == "VAR" and "addr" in n}
    why: str | None = _TASK if func.get("type") == "TASK" else None
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
    kind = "function" if nodes[first].get("type") == "FUNC" else "task"
    return Impure(name, file, line, f"the {kind} `{name}` ({file}, line {line}) {why[first]}")
