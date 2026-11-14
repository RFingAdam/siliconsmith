# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Map a line of MyHDL's generated Verilog back to the agent's `hdl/` function and line.

MyHDL writes one `always` block per decorated function and names it with a label:

    always @(count, ui_in) begin: remember          # a function of the top block
    always @(gate, count) begin: keeper0_remember   # a function of block `keeper`, instance 0

The label is `<block><n>_<function>` for every `@block` the function sits in (apart from the top
one), `<n>` being MyHDL's instance counter. So a finding at `src/counter.v:44` is found by looking
up the `begin: <label>` that encloses line 44 and then finding `function` in the `hdl/*.py` files.

The Python files are only parsed with `ast`; nothing in them is imported or run. This module is
used by the container worker (`emit._lint`, `synth`), where reading the agent's files is fine.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_SOURCE_BYTES = 1 << 20
MAX_FILES = 40

_ALWAYS_LABEL = re.compile(r"^\s*(?:always\b.*|initial\b.*?)\bbegin\s*:\s*(\w+)\s*$")
_BLOCK_END = re.compile(r"^end\b")


@dataclass(frozen=True)
class Function:
    """A function defined inside a function in `hdl/`: a MyHDL generator or a helper."""

    file: str  # project-relative, `hdl/counter.py`
    name: str
    line: int
    outer: str  # the enclosing function (the `@block`), "" for a module-level function
    node: ast.FunctionDef


def block_label(core_text: str, line: int) -> str | None:
    """The label of the `always` block that contains `line` (1-based), or None.

    MyHDL ends every generated block with an unindented `end`, so the nearest preceding label
    only counts if no unindented `end` comes between it and `line`.
    """
    lines = core_text.splitlines()
    for number in range(min(line, len(lines)), 0, -1):
        text = lines[number - 1]
        found = _ALWAYS_LABEL.match(text)
        if found:
            return found.group(1)
        if number != line and _BLOCK_END.match(text):
            return None
    return None


def read_functions(work: Path) -> list[Function]:
    """Every function defined in `work/hdl/*.py` (parsed, never run), generator functions first."""
    found: list[Function] = []
    files = sorted((work / "hdl").glob("*.py"))[:MAX_FILES]
    for path in files:
        try:
            if path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, ValueError, RecursionError, MemoryError):
            continue
        rel = f"hdl/{path.name}"

        def walk(node: ast.AST, outer: str, rel: str = rel) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef):
                    found.append(Function(rel, child.name, child.lineno, outer, child))
                    walk(child, child.name)
                else:
                    walk(child, outer)

        walk(tree, "")
    return found


def find_function(
    label: str, functions: list[Function], preferred_file: str = ""
) -> Function | None:
    """The function a MyHDL block label stands for (see the module docstring).

    The label is the function name alone (top block) or ends with `_<function>`; when several
    functions have that name, the one inside the block named by the label prefix wins, then one
    in `preferred_file`.
    """
    candidates = [
        f for f in functions if f.outer and (label == f.name or label.endswith(f"_{f.name}"))
    ]
    if not candidates:
        return None
    longest = max(len(f.name) for f in candidates)
    candidates = [f for f in candidates if len(f.name) == longest]
    if len(candidates) > 1:
        prefix = label[: -len(candidates[0].name)].rstrip("_")
        named = [
            f for f in candidates if re.fullmatch(rf"(?:\w+?\d+_)*{re.escape(f.outer)}\d+", prefix)
        ]
        candidates = named or candidates
    if len(candidates) > 1:
        local = [f for f in candidates if f.file == preferred_file]
        candidates = local or candidates
    return candidates[0]


def _assignment_line(function: Function, signal: str) -> int | None:
    """The line of `<signal>.next = ...` in the function, if it can be found.

    A signal that is passed in under another name (a port of a sub-block) is not found by name;
    then the function's only `.next` assignment is used, if it has just one.
    """
    named: list[int] = []
    every: list[int] = []
    for node in ast.walk(function.node):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "next":
                every.append(node.lineno)
                if isinstance(target.value, ast.Name) and target.value.id == signal:
                    named.append(node.lineno)
    if named:
        return min(named)
    return every[0] if len(every) == 1 else None


def map_line(
    core_text: str,
    line: int,
    name: str,
    signal: str = "",
    work: Path = Path("/work"),
    functions: list[Function] | None = None,
) -> dict[str, Any] | None:
    """`{file, line, function}` in `hdl/` for `src/<name>.v:<line>`, or None if it cannot be found.

    `signal` (the plain name of the signal the finding is about, if any) picks the line of its
    `.next =` assignment inside the function; otherwise the line is the function's `def`.
    """
    label = block_label(core_text, line)
    if label is None:
        return None
    funcs = read_functions(work) if functions is None else functions
    function = find_function(label, funcs, f"hdl/{name}.py")
    if function is None:
        return None
    where = _assignment_line(function, signal.rsplit(".", 1)[-1])
    return {"file": function.file, "line": where or function.line, "function": function.name}


def annotate(
    findings: list[dict[str, Any]],
    core_text: str,
    name: str,
    work: Path = Path("/work"),
) -> None:
    """Add `hdl_file`, `hdl_line` and `hdl_function` to every finding that is in `src/<name>.v`.

    The message gets ` (hdl/counter.py:21, function remember)` appended, so a summary that only
    prints `msg` still names the agent's file. Findings that cannot be mapped are left alone.
    """
    wanted = [f for f in findings if f.get("file") == f"src/{name}.v" and int(f.get("line") or 0)]
    if not wanted:
        return
    functions = read_functions(work)
    for item in wanted:
        signal = str(item.get("signal") or "")
        mapped = map_line(core_text, int(item["line"]), name, signal, work, functions)
        if mapped is None:
            continue
        item["hdl_file"], item["hdl_line"] = mapped["file"], mapped["line"]
        item["hdl_function"] = mapped["function"]
        where = f"{mapped['file']}:{mapped['line']}, function {mapped['function']}"
        key = "msg" if "msg" in item else "message"
        item[key] = f"{item[key]} ({where})"
