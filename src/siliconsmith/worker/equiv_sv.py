# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Read the RTL text the way a SIMULATOR does, for the equivalence check (stdlib only).

Synthesis reads more into a Verilog file than a simulator does: hot comments (`// synopsys
translate_off`, `full_case`), attributes (`(* nosync *)`, `(* nolatches *)`), predefined macros
(`ifdef SYNTHESIS`). The check wants the RTL side to be what a simulator runs, and the netlist to be
what synthesis made of the ORIGINAL text, so any real difference shows up as a difference.

* `clean(text)` returns the text with every comment and every attribute instance blanked out
  (spaces; newlines stay, so line numbers do not move). Strings and escaped identifiers are
  skipped over, and `@(*)` / `@( * )` is not taken for an attribute.
* `refusal(files)` looks at the same text (comments, attributes and string contents blanked) for
  what cannot be neutralized this way and returns the first such construct, or None. It is an
  ALLOW-list: a macro must be `define`d in the same files before it is tested with `ifdef`, only a
  short list of directives is accepted, and the rest is refused.

Refused (the plain reason names the construct and the line):

* `include (the check does not see the included file);
* `ifdef / `ifndef / `elsif on a macro the files did not `define earlier (SYNTHESIS, YOSYS,
  __ICARUS__, VERILATOR, FORMAL, ...: a simulator and the synthesis define different ones);
* any directive other than `timescale, `default_nettype, `define, `undef, `ifdef, `ifndef,
  `elsif, `else, `endif, `resetall, `celldefine, `endcelldefine and a macro defined earlier;
  token pasting with two backticks;
* the keywords `unique`, `unique0`, `priority` (synthesis assumes things a simulator does not);
* the two-state types `bit`, `byte`, `shortint`, `int`, `longint` (they start at 0 in a simulator,
  which is an initial value the check cannot see; use `reg` or `logic` and a reset);
* delay controls (`#5`, `#(2)`, `##1`): a simulator waits, synthesis ignores them. The `#(` of a
  parameter list (`module m #(`, `inst #(.W(8)) u (...)`) is fine;
* `$readmemh` / `$readmemb` (they read a file the check does not have) and
  `$test$plusargs` / `$value$plusargs` (simulator command-line options).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ALLOWED_DIRECTIVES = frozenset(
    {
        "timescale",
        "default_nettype",
        "define",
        "undef",
        "ifdef",
        "ifndef",
        "elsif",
        "else",
        "endif",
        "resetall",
        "celldefine",
        "endcelldefine",
    }
)
KEYWORDS = ("unique", "unique0", "priority")
TWO_STATE = ("bit", "byte", "shortint", "int", "longint")
SYSTEM_TASKS = ("$readmemh", "$readmemb", "$test$plusargs", "$value$plusargs")
# A `#` after one of these is a delay (`assign #1`, `and #2 g (...)`), not a parameter list.
_NOT_A_MODULE_NAME = frozenset(
    {
        "assign",
        "begin",
        "end",
        "else",
        "and",
        "or",
        "nand",
        "nor",
        "xor",
        "xnor",
        "not",
        "buf",
        "bufif0",
        "bufif1",
        "notif0",
        "notif1",
        "wire",
        "wand",
        "wor",
        "tri",
        "tri0",
        "tri1",
        "triand",
        "trior",
        "trireg",
        "supply0",
        "supply1",
        "force",
        "release",
        "deassign",
        "forever",
        "fork",
        "join",
        "initial",
        "always",
        "always_comb",
        "always_ff",
        "always_latch",
        "then",
        "case",
        "default",
    }
)


@dataclass(frozen=True)
class Refusal:
    """The first construct the check cannot vouch for."""

    code: str  # "include" or "sim_synth"
    what: str  # the construct, in plain words
    file: str
    line: int


def _blank(segment: str) -> str:
    return "".join(c if c == "\n" else " " for c in segment)


def _string_end(text: str, i: int) -> int:
    """Index just past the string that starts at `text[i] == '"'`."""
    n = len(text)
    j = i + 1
    while j < n:
        if text[j] == "\\":
            j += 2
        elif text[j] == '"':
            return j + 1
        elif text[j] == "\n":
            return j  # an unterminated string ends at the line end (Yosys will refuse it)
        else:
            j += 1
    return n


def lex(text: str) -> tuple[str, str]:
    """`(clean, scan)`: the text without comments and attributes, and the same with the contents
    of strings and escaped identifiers blanked as well (what `refusal` looks at)."""
    n = len(text)
    clean: list[str] = []
    scan: list[str] = []
    i = 0
    while i < n:
        c = text[i]
        two = text[i : i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j < 0 else j
            clean.append(_blank(text[i:j]))
            scan.append(_blank(text[i:j]))
        elif two == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            clean.append(_blank(text[i:j]))
            scan.append(_blank(text[i:j]))
        elif c == '"':
            j = _string_end(text, i)
            clean.append(text[i:j])
            scan.append('"' + _blank(text[i + 1 : j - 1]) + '"' if j - i >= 2 else text[i:j])
        elif c == "\\" and i + 1 < n and not text[i + 1].isspace():
            j = i + 1
            while j < n and not text[j].isspace():
                j += 1
            clean.append(text[i:j])
            scan.append("\\" + "x" * (j - i - 1))
        elif two == "(*" and not re.match(r"\(\*[ \t]*\)", text[i:]):
            j = i + 2
            while j < n and text[j : j + 2] != "*)":
                j = _string_end(text, j) if text[j] == '"' else j + 1
            j = n if j >= n else j + 2
            clean.append(_blank(text[i:j]))
            scan.append(_blank(text[i:j]))
        else:
            clean.append(c)
            scan.append(c)
            j = i + 1
        i = j
    return "".join(clean), "".join(scan)


def clean(text: str) -> str:
    """The RTL as a simulator reads it: comments and attribute instances blanked out."""
    return lex(text)[0]


def _define_bodies(scan: str) -> list[tuple[int, int]]:
    """Character ranges of the macro definitions (to the line end, `\\` continues the line)."""
    ranges = []
    for m in re.finditer(r"`define\b", scan):
        j = m.end()
        while True:
            k = scan.find("\n", j)
            if k < 0:
                j = len(scan)
                break
            if scan[j:k].rstrip().endswith("\\"):
                j = k + 1
                continue
            j = k
            break
        ranges.append((m.start(), j))
    return ranges


def _line(scan: str, pos: int) -> int:
    return scan.count("\n", 0, pos) + 1


def _delay(scan: str, pos: int, in_define: bool) -> bool:
    """True if the `#` at `scan[pos]` is a delay, not the start of a parameter list."""
    j = pos + 1
    while j < len(scan) and scan[j] in " \t\r\n":
        j += 1
    if in_define or scan[j : j + 1] != "(" or scan[pos + 1 : pos + 2] == "#":
        return True
    k = pos - 1
    while k >= 0 and scan[k] in " \t\r\n":
        k -= 1
    end = k + 1
    while k >= 0 and (scan[k].isalnum() or scan[k] in "_$"):
        k -= 1
    word = scan[k + 1 : end]
    return not word or word in _NOT_A_MODULE_NAME or word[0].isdigit()


_DIRECTIVE = re.compile(r"``|`([A-Za-z_]\w*)?")


def refusal(files: list[tuple[str, str]]) -> Refusal | None:
    """The first construct in the files (read in this order) that the check cannot vouch for."""
    defined: set[str] = set()
    for name, text in files:
        scan = lex(text)[1]
        bodies = _define_bodies(scan)

        def refuse(code: str, what: str, pos: int, name: str = name, scan: str = scan) -> Refusal:
            return Refusal(code, what, name, _line(scan, pos))

        for m in _DIRECTIVE.finditer(scan):
            word = m.group(1)
            if m.group(0) == "``" or word is None:
                return refuse("sim_synth", "a stray backtick or `` token pasting", m.start())
            if word == "include":
                return refuse("include", "`include", m.start())
            rest = re.match(r"\s*([A-Za-z_]\w*)", scan[m.end() :])
            macro = rest.group(1) if rest else ""
            if word in ("ifdef", "ifndef", "elsif"):
                if macro not in defined:
                    return refuse("sim_synth", f"`{word} {macro or '?'}", m.start())
            elif word == "define":
                defined.add(macro)
            elif word == "undef":
                defined.discard(macro)
            elif word not in ALLOWED_DIRECTIVES and word not in defined:
                return refuse("sim_synth", f"the directive `{word}", m.start())
        for m in re.finditer(r"(?<![\w$])(" + "|".join(KEYWORDS + TWO_STATE) + r")(?![\w$])", scan):
            kind = "keyword" if m.group(1) in KEYWORDS else "two-state type"
            return refuse("sim_synth", f"the {kind} `{m.group(1)}`", m.start())
        for task in SYSTEM_TASKS:
            at = scan.find(task)
            if at >= 0:
                return refuse("sim_synth", f"`{task}`", at)
        for m in re.finditer(r"#", scan):
            if _delay(scan, m.start(), any(a <= m.start() < b for a, b in bodies)):
                return refuse("sim_synth", "a delay (`#`)", m.start())
    return None


def plain_message(found: Refusal) -> str:
    """What to tell the agent, in plain English."""
    if found.code == "include":
        return (
            f"the RTL ({found.file}, line {found.line}) uses `include. The check only reads "
            "project.v and the design file, so it cannot see what an included file does. Put "
            "the code in the design file."
        )
    advice = {
        "`ifdef": "Remove it: a simulator and the synthesis define different macros.",
        "`ifndef": "Remove it: a simulator and the synthesis define different macros.",
        "`elsif": "Remove it: a simulator and the synthesis define different macros.",
        "two-state": "Use `reg` or `logic` and reset it with rst_n: a two-state type starts at "
        "0 in a simulator, which the check cannot see.",
        "keyword": "Use a plain `case` or `if`: synthesis assumes things a simulator does not.",
        "delay": "Remove it: a simulator waits, synthesis ignores it.",
        "$": "Remove it: it reads a file or an option the check does not have.",
    }
    hint = next((v for k, v in advice.items() if k in found.what), "Remove it.")
    return (
        f"the RTL ({found.file}, line {found.line}) uses {found.what}, which a simulator and "
        f"the synthesis read differently, so the check cannot vouch for it. {hint}"
    )
