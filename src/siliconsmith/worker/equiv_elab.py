# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Does Verilator's elaboration hold every block that Yosys elaborates?

The equivalence proof compares the netlist with the RTL as Yosys reads it. Two of the refusals
before the proof (a function that reads more than its arguments, an `always @*` that reads a
signal before it writes it) are judged on what Verilator makes of the RTL. The two tools can
elaborate the RTL differently: a constant that is x to Yosys and to a simulator (`1/0`, `5 % 0`,
`0 ** -1`) is folded by Verilator to some value, so it can pick another branch of a generate
`if`, `case` or `for`, or another module instance. Then those two checks would judge a different
design from the one the proof and a simulator use.

This module compares the two elaborations by position. Yosys writes the RTLIL of the elaborated
design (`write_rtlil`), and every process in it carries the source position of the `always` block
or function call it comes from. Verilator's tree (`--json-only`) has a node for every `always`
block and every function call it kept, with a position in the same files. Each block Yosys keeps
must have a node of the same kind at the same position in Verilator's tree, or the check refuses
(a generate branch or module that Yosys uses and Verilator dropped). A generate branch that is
really not taken by both tools is no problem: neither has its blocks.

Each process position is read in the cleaned text (`anchor`), looking past blanks and line ends
(`rd\n(ui_in)` is a call), and must be one of a few known kinds: an always block, an `initial`
or `final` (no requirement), a declaration with a value, a function or task call, an element of
an array, or a `.name(` port connection. A declaration, an array element or a port connection
has a process of its own, and every call in its value must have a call node in Verilator's tree
(a call that Verilator removes as dead code, such as `EN ? f(a) : a`, `EN && f(a)` or
`f(a) & {8{EN}}` with a constant `EN`, does not). Any other position cannot be placed and is a
refusal: never a pass.

A hierarchical function call (`g.f(a)`) is refused too: it can resolve to a different function
in a generate branch that the two tools choose differently, and positions would not show it.

No subprocess here: the RTLIL and the tree are passed in as text and dicts. Fail closed: input
that cannot be read raises `ElabError` (or `equiv_tree.TreeError`), which the caller turns into a
refusal.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from siliconsmith.worker import equiv_tree


class ElabError(Exception):
    """The RTLIL is not in a shape this module understands."""


class Anchor(NamedTuple):
    """The word a process position points at: its kind (`always`, `call`, ...), the word and where
    it starts."""

    kind: str
    word: str
    line: int
    col: int


_SRC_ATTRIBUTE = re.compile(r'^\s*attribute\s+\\src\s+"(.*)"\s*$')
_SRC_PART = re.compile(r"^(.+):(\d+)\.(\d+)-(\d+)\.(\d+)$")
_PROCESS = re.compile(r"^\s*process\s")
_ATTRIBUTE = re.compile(r"^\s*attribute\s")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*", re.ASCII)
_CALL = re.compile(r"(?<![A-Za-z0-9_$'\\])([A-Za-z_][A-Za-z0-9_$]*)\s*\(", re.ASCII)
_ESCAPED_CALL = re.compile(r"(?<![^\s(\[{,;=?:~!&|^+\-*/%<>])(\\\S+)\s+\(", re.ASCII)
ASCII_BLANKS = " \t\r\n"
_IDENT_AT_END = re.compile(r"(?<![A-Za-z0-9_$])[A-Za-z_][A-Za-z0-9_$]*$", re.ASCII)
ALWAYS_WORDS = frozenset({"always", "always_comb", "always_ff", "always_latch"})
NO_REQUIREMENT = frozenset({"initial", "final"})  # they matter to neither check


class Process(NamedTuple):
    """The source span of a process: where it starts (`line`, `col`) and ends."""

    file: str
    line: int
    col: int
    end_line: int
    end_col: int


def processes(rtlil: str) -> list[Process]:
    """`(file, line, column, end line, end column)` of the source of every process in the RTLIL
    text, in the order of the text. All attribute lines in front of a `process` line belong to it,
    and a `src` value may hold several parts joined by `|`: every part is returned. A process
    without a readable `src` raises `ElabError`."""
    found: list[Process] = []
    sources: list[str] = []
    for line in rtlil.splitlines():
        if _PROCESS.match(line):
            parts = [part for value in sources for part in value.split("|")]
            places = [_SRC_PART.match(part) for part in parts]
            if not parts or None in places:
                raise ElabError("a process in the elaborated RTL has no readable source position")
            for match in places:
                if match is not None:
                    found.append(Process(match.group(1), *(int(x) for x in match.groups()[1:])))
            sources = []
        elif _ATTRIBUTE.match(line):
            match = _SRC_ATTRIBUTE.match(line)
            if match is not None:
                sources.append(match.group(1))
        else:
            sources = []
    return found


def _offset(text: str, line: int, col: int) -> int:
    """The index in `text` of a 1-based line and column."""
    return sum(len(row) + 1 for row in text.split("\n")[: line - 1]) + col - 1


def _line_col(text: str, at: int) -> tuple[int, int]:
    return text.count("\n", 0, at) + 1, at - (text.rfind("\n", 0, at) + 1) + 1


_PORT = re.compile(r"\.\s*(\\\S+|[A-Za-z_][A-Za-z0-9_$]*)\s*\(", re.ASCII)


def _port(text: str, at: int) -> Anchor | None:
    """A `.name(` port connection at `at` or after the blanks and line ends that follow it, as
    the start of an instance's port (the last thing before it, past blanks, is `(` or `,`)."""
    start = at
    while start < len(text) and text[start] in ASCII_BLANKS:
        start += 1
    found = _PORT.match(text, start)
    before = text[:start].rstrip(ASCII_BLANKS)
    if found is None or not before or before[-1] not in "(,":
        return None
    return Anchor("port", found.group(1), *_line_col(text, found.start(1)))


def anchor(text: str, line: int, col: int, zero_width: bool = False) -> Anchor | None:
    """The word that a Yosys source position points at in the cleaned text, or None.

    An identifier that STARTS at the position is the anchor, and so is an escaped identifier
    (`\\name`, up to the next blank). Otherwise, and first when the position has no width
    (`zero_width`: Yosys puts an `always_comb`, `always_latch` or `initial` at the spot just after
    the keyword, which may be the start of an escaped name), an identifier that ENDS right before
    it is; the anchor is then that identifier at its own start. A line counts columns from 1, a
    tab as one column. What follows the word is looked for past blanks and line ends. Kinds:

    * `always` for the four always keywords, `skip` for `initial` and `final`;
    * `decl`: a declared name with a value (`reg x = 0;` is a process of its own, starting at the
      name); `=` follows;
    * `call`: a function or task call outside an always block (a process of its own, starting at
      the name, or at the package or scope name): `(`, `.` or `::` follows; for `pkg::f(a)` the
      anchor is `f`, where Verilator puts it;
    * `array`: an element of a register or wire array that Yosys turns into a process (`[`
      follows);
    * `port`: a `.name(` port connection that the position starts at, or is on the blanks before
      (Yosys makes a process for an output port connected to an element of a wire array; a
      connection over several lines can have a column past the end of its row, which is read as
      the end of the row);
    * `other`: anything else (the caller cannot place it)."""
    lines = text.split("\n")
    if not 1 <= line <= len(lines) or col < 1:
        return None
    row = lines[line - 1]
    col = min(col, len(row) + 1)
    here = row[col - 1 :]
    started: tuple[str, int] | None = None
    if here.startswith("\\"):
        started = (re.split(r"\s", here, maxsplit=1)[0], col)
    else:
        word = _IDENT.match(here)
        before = row[col - 2] if col >= 2 else " "
        if word is not None and re.match(r"[A-Za-z0-9_$]", before) is None:
            started = (word.group(0), col)
    prefix = row[: col - 1]
    if (
        (started is None or zero_width)
        and re.match(r"[A-Za-z0-9_$]", here) is None  # not inside a word
        and prefix
        and not prefix[-1].isspace()
    ):
        last = prefix.rsplit(None, 1)[-1]
        if last.startswith("\\"):
            started = (last, len(prefix) - len(last) + 1)
        else:
            tail = _IDENT_AT_END.search(prefix)
            if tail is not None:
                started = (tail.group(0), tail.start() + 1)
    if started is None:
        return _port(text, _offset(text, line, col)) if not here or here[0] in " \t\r." else None
    word_text, start = started
    end = _offset(text, line, start) + len(word_text)
    rest = text[end:]
    scoped = re.match(r"\s*::\s*", rest)
    if scoped is not None:
        member = _IDENT.match(rest[scoped.end() :])
        if member is None:
            return None
        return Anchor("call", member.group(0), *_line_col(text, end + scoped.end()))
    if word_text in ALWAYS_WORDS:
        kind = "always"
    elif word_text in NO_REQUIREMENT:
        kind = "skip"
    elif re.match(r"\s*=(?!=)", rest):
        kind = "decl"
    elif re.match(r"\s*[(.]", rest):
        kind = "call"
    elif re.match(r"\s*\[", rest):
        kind = "array"
    else:
        kind = "other"
    return Anchor(kind, word_text, line, start)


def initializer_calls(text: str, found: Anchor) -> list[Anchor]:
    """The calls after an anchor, up to the `,` or `;` that ends its value, or the bracket that
    closes around it: the value of a declaration (`reg [7:0] x = f(a), y = 3;`: those of
    `x = ...`), the index and value of an array element (`assign m[f(i)] = g(a);`) or the
    expression of a port connection (`.q(f(a))`). Yosys gives each of them one process, anchored
    at the name, that holds its calls; Verilator has a call node for each. Brackets are counted,
    and an escaped name runs to the next blank, whatever it holds."""
    base = _offset(text, found.line, found.col) + len(found.word)
    end = len(text)
    depth = 0
    at = base
    while at < len(text):
        if text[at] == "\\":  # an escaped name runs to the next blank, whatever it holds
            while at < len(text) and not text[at].isspace():
                at += 1
            continue
        if text[at] in "([{":
            depth += 1
        elif text[at] in ")]}":
            depth -= 1
            if depth < 0:
                end = at
                break
        elif depth <= 0 and text[at] in ",;":
            end = at
            break
        at += 1
    calls = []
    for call in _CALL.finditer(text, base, end):
        calls.append(Anchor("call", call.group(1), *_line_col(text, call.start(1))))
    for call in _ESCAPED_CALL.finditer(text, base, end):
        calls.append(Anchor("call", call.group(1), *_line_col(text, call.start(1))))
    return sorted(calls, key=lambda c: (c.line, c.col))


def _unplaceable(file: str, line: int) -> str:
    return (
        "Yosys and Verilator elaborate the RTL differently, or the check cannot tell: it cannot "
        f"place a block that Yosys keeps ({file}, line {line}) in Verilator's elaboration. "
        "The checks of functions and of combinational blocks read Verilator's elaboration, so "
        "they cannot vouch for it."
    )


def _missing(what: str, file: str, line: int) -> str:
    return (
        f"Yosys and Verilator elaborate the RTL differently: Yosys keeps the {what} at {file}, "
        f"line {line}, which Verilator's elaboration does not have (a generate branch or module "
        "instance chosen by a constant that is x to Yosys and a simulator, such as `1/0`, or a "
        "call that Verilator removes as dead code, such as `EN ? f(a) : a`, `EN && f(a)` or "
        "`f(a) & {8{EN}}` with a constant `EN`). "
        "The checks of functions and of combinational blocks read Verilator's elaboration, so "
        "they cannot vouch for it. Give the constant a defined value, or choose with a generate "
        "`if` instead of a constant inside an expression."
    )


def unmatched(tree: Any, meta: Any, rtlil: str, cleaned: dict[str, str]) -> str | None:
    """A plain-English sentence if Verilator's elaboration does not hold a block that Yosys
    elaborates (or a call the check cannot trust), else None.

    `rtlil` is Yosys's `write_rtlil` of the elaborated RTL, `cleaned` maps each of our file names
    (in order) to the cleaned text Yosys read. The first problem is reported, by file order, then
    line, then column. Raises `ElabError` or `equiv_tree.TreeError` if the input is unreadable."""
    placed = list(equiv_tree.placed_nodes(tree, meta))
    always = {(n.keyword, n.file, n.line, n.col) for n in placed if n.type == "ALWAYS"}
    calls = {(n.file, n.line, n.col) for n in placed if n.type in ("FUNCREF", "TASKREF")}
    order = list(cleaned)
    dotted = [
        n for n in placed if n.type in ("FUNCREF", "TASKREF") and n.dotted and n.file in cleaned
    ]
    if dotted:  # first, as the clearer reason: the position of such a call never matches Yosys's
        n = min(dotted, key=lambda n: (order.index(n.file), n.line, n.col))
        return (
            f"the RTL calls a function by a hierarchical name (`{n.dotted}.{n.name}`, {n.file}, "
            f"line {n.line}): it can resolve to another function in a generate branch that "
            "Yosys and Verilator choose differently, so the check cannot vouch for it. Call the "
            "function from inside its own block."
        )
    problems: list[tuple[int, int, int, str]] = []
    for span in dict.fromkeys(processes(rtlil)):
        file, line, col = span[:3]
        found = anchor(cleaned[file], line, col, span[3:] == span[1:3]) if file in cleaned else None
        where = order.index(file) if file in cleaned else len(order)
        if found is None or found.kind == "other":
            problems.append((where, line, col, _unplaceable(file, line)))
        elif found.kind == "always" and (found.word, file, found.line, found.col) not in always:
            problems.append((where, line, col, _missing("always block", file, line)))
        elif found.kind == "call" and (file, found.line, found.col) not in calls:
            what = f"call of `{found.word}`"
            problems.append((where, line, col, _missing(what, file, line)))
        elif found.kind in ("decl", "array", "port"):
            for inner in initializer_calls(cleaned[file], found):
                if (file, inner.line, inner.col) not in calls:
                    what = f"call of `{inner.word}`"
                    problems.append(
                        (where, inner.line, inner.col, _missing(what, file, inner.line))
                    )
    return min(problems)[3] if problems else None
