# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Prepare the RTL text for the equivalence check, and refuse what the check cannot vouch for.

## What the check promises (stdlib only, runs in the container)

PROVEN: the netlist implements the RTL as Yosys reads it, once comments, attributes and macros are
out of the way. In plain words: synthesis did not change your logic.

BEST EFFORT: Yosys and a simulator do not read every Verilog file the same way. `refusal()` spots
the common constructs where they differ and the check answers `not_proven` (with the construct
named) instead of a verdict. What it does NOT spot is left to gate-level simulation
(`run_tests` with `gl=true`):

* an out-of-range index or a division by zero (x in a simulator, some value in the gates, because
  an x in the RTL is a don't-care);
* anything that depends on the order in which a simulator runs events.

## What this module does

* `lex(text)` returns `(clean, scan, problem)`. `clean` is the text with every comment and every
  attribute instance `(* ... *)` blanked out (spaces; newlines stay, so line numbers do not move):
  Yosys then reads no hot comment (`// synopsys translate_off`) and no attribute (`nosync`,
  `full_case`). `scan` is the same with the contents of strings and escaped identifiers blanked
  too. `problem` is the first thing the tokenizer cannot read safely (an unterminated comment,
  attribute or string; an attribute that holds `;`, a backtick or a comment).
* `refusal(files, strict)` returns the first construct in the files that is refused, or None.

## Refused (`refusal`)

In every file:

* the macro machinery: every backtick directive except `timescale, `default_nettype, `resetall,
  `celldefine and `endcelldefine (so no `define, `ifdef, macro use, `` pasting, `line, `pragma);
  `include has its own reason. A macro can build text the check never sees; use a parameter;
* the keywords `unique`, `unique0`, `priority`, `primitive`, `casex`, `inside`;
* the two-state types `bit`, `byte`, `shortint`, `int`, `longint`, and an `enum` without a
  four-state base type (`enum logic [1:0] {...}` is fine): they start at 0 in a simulator;
* `===` and `!==`: the check models them differently from a simulator;
* `==?`, `!=?` and `inside` (wildcard comparisons);
* x digits in a based literal (`4'b1x0`, `'hx`) and the unbased `'x`; z or ? digits and `'z`
  anywhere but as the label of an item of a `casez` (innermost case is a `casez`, outside the
  header, at bracket depth 0, after `;` `,` `end` `endcase` or the header `)`, before `,` or `:`):
  there they are wildcards for both a simulator and synthesis;
* every character outside printable ASCII, tab, CR and LF that is not in a comment (the tools
  disagree about where an escaped identifier ends and what counts as a blank);
* delay controls (`#`), except a parameter list in exactly two shapes:
  `module NAME #(` and `NAME #( ... ) INSTANCE [range] (`; neither name may be a reserved word;
* `$readmemh`, `$readmemb`, `$test$plusargs`, `$value$plusargs`;
* a `// verilator lint_off` comment with no code, or with a code that hides what the lint step
  catches (BLKSEQ, COMBDLY, CASEX, CASEWITHX, CASEINCOMPLETE, LATCH, MULTIDRIVEN, ...): the lint
  runs with every warning fatal, and that is what finds a blocking race or an x in a case item.

Only in a core file that lists no level signals itself (`strict_core`: Verilog and Amaranth; never
`project.v`, which the tool writes):

* every event control that is not `@*`, `@(*)` or only `posedge`/`negedge` items (`always @(a or
  b)`): a simulator does not rerun the block for a signal missing from the list, the gates do not
  care about the list;
* `function` or `task` without `automatic`: a simulator keeps the local variables of a plain
  function between calls.

MyHDL writes `@always_comb` as `always @(a, b)`, so its core is not judged on event lists; the
`hdl/` Python source is parsed instead (`myhdl_map.level_sensitive`).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass

ALLOWED_DIRECTIVES = frozenset(
    {"timescale", "default_nettype", "resetall", "celldefine", "endcelldefine"}
)
KEYWORDS = ("unique", "unique0", "priority", "primitive", "casex", "inside")
TWO_STATE = ("bit", "byte", "shortint", "int", "longint")
SYSTEM_TASKS = ("$readmemh", "$readmemb", "$test$plusargs", "$value$plusargs")
# verilator lint codes whose warnings (fatal in our lint step) catch what a text scan cannot
LINT_CODES_NOT_TO_HIDE = frozenset(
    {
        "BLKSEQ", "BLKANDNBLK", "COMBDLY", "CASEX", "CASEWITHX", "CASEINCOMPLETE", "CASEOVERLAP",
        "LATCH", "NOLATCH", "MULTIDRIVEN", "SYNCASYNCNET", "INITIALDLY", "ALWCOMBORDER",
        "IMPLICITSTATIC",
    }
)  # fmt: skip
# Verilog-2005 and SystemVerilog reserved words: neither the module name of a parameter list
# nor an instance name may be one of them.
RESERVED = frozenset(
    (  # noqa: SIM905
        "accept_on alias always always_comb always_ff always_latch and assert assign assume "
        "automatic before begin bind bins binsof bit break buf bufif0 bufif1 byte case casex "
        "casez cell chandle checker class clocking cmos config const constraint context "
        "continue cover covergroup coverpoint cross deassign default defparam design disable "
        "dist do edge else end endcase endchecker endclass endclocking endconfig endfunction "
        "endgenerate endgroup endinterface endmodule endpackage endprimitive endprogram "
        "endproperty endsequence endspecify endtable endtask enum event eventually expect "
        "export extends extern final first_match for force foreach forever fork forkjoin "
        "function generate genvar global highz0 highz1 if iff ifnone ignore_bins illegal_bins "
        "implements implies import incdir include initial inout input inside instance int "
        "integer interconnect interface intersect join join_any join_none large let liblist "
        "library local localparam logic longint macromodule matches medium modport module "
        "nand negedge nettype new nexttime nmos nor noshowcancelled not notif0 notif1 null or "
        "output package packed parameter pmos posedge primitive priority program property "
        "protected pull0 pull1 pulldown pullup pulsestyle_ondetect pulsestyle_onevent pure "
        "rand randc randcase randsequence rcmos real realtime ref reg reject_on release "
        "repeat restrict return rnmos rpmos rtran rtranif0 rtranif1 s_always s_eventually "
        "s_nexttime s_until s_until_with scalared sequence shortint shortreal showcancelled "
        "signed small soft solve specify specparam static string strong strong0 strong1 "
        "struct super supply0 supply1 sync_accept_on sync_reject_on table tagged task this "
        "throughout time timeprecision timeunit tran tranif0 tranif1 tri tri0 tri1 triand "
        "trior trireg type typedef union unique unique0 unsigned until until_with untyped use "
        "uwire var vectored virtual void wait wait_order wand weak weak0 weak1 while wildcard "
        "wire with within wor xnor xor"
    ).split()
)


@dataclass(frozen=True)
class Refusal:
    """The first construct the check cannot vouch for."""

    code: str  # "include" or "sim_synth"
    what: str  # the construct, in plain words
    file: str
    line: int


# Whitespace is exactly these four characters, as in the Yosys, Icarus and Verilator lexers
# (Python's `str.isspace()` also takes NBSP, NEL, U+2028, ...: a boundary the tools do not see).
ASCII_BLANKS = " \t\r\n"
_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")


def _bad_char(segment: str) -> str | None:
    """The first character outside printable ASCII, tab, CR and LF (None if there is none)."""
    return next((c for c in segment if c not in ASCII_BLANKS and not " " <= c <= "~"), None)


def _blank(segment: str) -> str:
    return "".join(c if c == "\n" else " " for c in segment)


def _string_end(text: str, i: int) -> int | None:
    """Index just past the string that starts at `text[i] == '"'`; None if it is not closed on
    this line (a raw line break or the end of the file comes first)."""
    n = len(text)
    j = i + 1
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
        elif c == '"':
            return j + 1
        elif c == "\n":
            return None
        else:
            j += 1
    return None


def lex(text: str) -> tuple[str, str, tuple[int, str] | None]:
    """`(clean, scan, problem)`: see the module docstring. `problem` is `(position, what)`."""
    n = len(text)
    clean: list[str] = []
    scan: list[str] = []
    problem: tuple[int, str] | None = None
    last = ""  # the last significant character so far (not blank, not in a comment)
    i = 0

    def note(pos: int, what: str) -> None:
        nonlocal problem
        if problem is None:
            problem = (pos, what)

    def code(segment: str, pos: int) -> None:
        """Outside a comment every character must be printable ASCII, tab, CR or LF: the tools
        disagree about the rest (where an escaped identifier ends, what counts as a blank)."""
        bad = _bad_char(segment)
        if bad is not None:
            shown = f"U+{ord(bad):04X}"
            note(pos + segment.index(bad), f"a character outside plain ASCII ({shown}) in code")

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
            if j < 0:
                note(i, "a /* comment that is never closed")
                j = n
            else:
                j += 2
            clean.append(_blank(text[i:j]))
            scan.append(_blank(text[i:j]))
        elif c == '"':
            end = _string_end(text, i)
            if end is None:
                note(i, "a string with a line break in it, or one that is never closed")
                end = text.find("\n", i)
                end = n if end < 0 else end
            code(text[i:end], i)
            clean.append(text[i:end])
            scan.append('"' + _blank(text[i + 1 : end - 1]) + '"' if end - i >= 2 else text[i:end])
            last = '"'
            j = end
        elif c == "\\" and i + 1 < n and text[i + 1] not in ASCII_BLANKS:
            j = i + 1
            while j < n and text[j] not in ASCII_BLANKS:
                j += 1
            code(text[i:j], i)
            clean.append(text[i:j])
            scan.append("\\" + "x" * (j - i - 1))
            last = "x"
        elif two == "(*" and last == "@":
            clean.append("(")  # the event `@(*)`: `*` and `)` are ordinary characters
            scan.append("(")
            last = "("
            j = i + 1
        elif two == "(*":
            k = i + 2
            closed = False
            while k < n:
                if text[k : k + 2] == "*)":
                    closed = True
                    break
                if text[k] == '"':
                    end = _string_end(text, k)
                    k = k + 1 if end is None else end
                    continue
                if text[k] == ";" or text[k] == "`" or text[k : k + 2] in ("(*", "//", "/*"):
                    note(i, "an attribute (* ... *) that holds `;`, a backtick or a comment")
                k += 1
            if not closed:
                note(i, "an attribute (* that is never closed")
            j = k + 2 if closed else n
            code(text[i:j], i)
            clean.append(_blank(text[i:j]))
            scan.append(_blank(text[i:j]))
        else:
            code(c, i)
            clean.append(c)
            scan.append(c)
            if c not in ASCII_BLANKS:
                last = c
            j = i + 1
        i = j
    return "".join(clean), "".join(scan), problem


def clean(text: str) -> str:
    """The RTL the proof reads: comments and attribute instances blanked out."""
    return lex(text)[0]


_DIRECTIVE = re.compile(r"``|`([A-Za-z_]\w*)?", re.ASCII)
_WORD = re.compile(r"(?<![\w$'])[A-Za-z_][\w$]*", re.ASCII)
_BASED = re.compile(
    r"(?<![\w$])(?:\d[\d_]*\s*)?'\s*[sS]?\s*([bBoOdDhH])\s*([0-9a-fA-FxXzZ?_]+)", re.ASCII
)
_UNBASED = re.compile(r"'[xXzZ](?![\w])", re.ASCII)
_LINT_OFF = re.compile(r"verilator\s+lint_off\b([^\n*]*)", re.I | re.ASCII)


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _word_before(scan: str, pos: int) -> tuple[str, int]:
    """The identifier that ends right before `pos` (blanks skipped) and where it starts."""
    k = pos - 1
    while k >= 0 and scan[k] in ASCII_BLANKS:
        k -= 1
    end = k + 1
    while k >= 0 and scan[k] in _ID_CHARS:
        k -= 1
    return scan[k + 1 : end], k + 1


def _skip_blank(scan: str, k: int) -> int:
    while k < len(scan) and scan[k] in ASCII_BLANKS:
        k += 1
    return k


def _balanced(scan: str, k: int, open_c: str, close_c: str) -> int | None:
    """Index just past the group that starts at `scan[k] == open_c`, or None."""
    depth = 0
    while k < len(scan):
        if scan[k] == open_c:
            depth += 1
        elif scan[k] == close_c:
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    return None


def _parameter_list(scan: str, pos: int) -> bool:
    """True if the `#` at `scan[pos]` starts a parameter list in one of the two accepted shapes:
    `module [automatic|static] NAME #(` and `NAME #( ... ) INSTANCE [range] (`."""
    after = _skip_blank(scan, pos + 1)
    if scan[after : after + 1] != "(":
        return False
    name, start = _word_before(scan, pos)
    if (
        not name
        or name[0] in "0123456789"
        or name in RESERVED
        or (start > 0 and scan[start - 1] == "`")
    ):
        return False
    before, at = _word_before(scan, start)
    if before in ("automatic", "static"):
        before, at = _word_before(scan, at)
    if before in ("module", "macromodule"):
        return True
    close = _balanced(scan, after, "(", ")")
    if close is None:
        return False
    k = _skip_blank(scan, close)
    m = re.match(r"[A-Za-z_][\w$]*", scan[k:], re.ASCII)
    if m is None or m.group(0) in RESERVED:
        return False
    k = _skip_blank(scan, k + m.end())
    while scan[k : k + 1] == "[":  # an array of instances
        end = _balanced(scan, k, "[", "]")
        if end is None:
            return False
        k = _skip_blank(scan, end)
    return scan[k : k + 1] == "("


def _case_constructs(scan: str) -> list[tuple[int, int, int, str]]:
    """Every case statement as `(keyword start, body start, end, kind)`; the body starts after
    the header's closing parenthesis. Nested cases are listed too."""
    found: list[tuple[int, int, int, str]] = []
    stack: list[tuple[int, int, str]] = []
    for m in re.finditer(r"(?<![\w$])(casez|casex|case|endcase)(?![\w$])", scan, re.ASCII):
        word = m.group(1)
        if word == "endcase":
            if stack:
                start, body, kind = stack.pop()
                found.append((start, body, m.end(), kind))
            continue
        k = _skip_blank(scan, m.end())
        close = _balanced(scan, k, "(", ")") if scan[k : k + 1] == "(" else None
        stack.append((m.start(), close or m.end(), word))
    return found


def _wildcard_ok(scan: str, cases: list[tuple[int, int, int, str]], start: int, end: int) -> bool:
    """True if the z or ? literal at `scan[start:end]` is a label of a `casez` item.

    All of these must hold: the innermost enclosing case is a `casez` and the literal is in its
    body (not its header); it is at bracket depth 0 of that body; the token before it is `;`,
    `,`, `end`, `endcase` or the header's closing parenthesis; the one after it is `,` or `:`.
    """
    inside = [c for c in cases if c[0] <= start < c[2]]
    if not inside:
        return False
    _, body, _, kind = max(inside, key=lambda c: c[0])
    if kind != "casez" or start < body:
        return False
    depth = sum(ch in "([{" for ch in scan[body:start]) - sum(
        ch in ")]}" for ch in scan[body:start]
    )
    if depth != 0:
        return False
    k = start - 1
    while k >= body - 1 and scan[k] in ASCII_BLANKS:
        k -= 1
    word, _ = _word_before(scan, start)
    if not (scan[k] in ";," or (scan[k] == ")" and k == body - 1) or word in ("end", "endcase")):
        return False
    return scan[_skip_blank(scan, end) : _skip_blank(scan, end) + 1] in (",", ":")


def _event_controls(scan: str) -> int | None:
    """The position of the first event control that is not `@*`, `@(*)` or only edge items."""
    for m in re.finditer(r"@", scan):
        k = _skip_blank(scan, m.end())
        if scan[k : k + 1] == "*":
            continue
        if scan[k : k + 1] != "(":
            return m.start()
        end = _balanced(scan, k, "(", ")")
        if end is None:
            return m.start()
        inner = scan[k + 1 : end - 1].strip()
        if inner == "*":
            continue
        items, depth, cur = [], 0, ""
        for tok in re.split(r"(\(|\)|\[|\]|\{|\}|,|(?<![\w$])or(?![\w$]))", inner, flags=re.ASCII):
            if tok in ("(", "[", "{"):
                depth += 1
            elif tok in (")", "]", "}"):
                depth -= 1
            elif depth == 0 and (tok == "," or tok == "or"):
                items.append(cur)
                cur = ""
                continue
            cur += tok
        items.append(cur)
        edge = r"\s*(posedge|negedge)\s+\S"
        if not all(
            re.match(edge, it, re.ASCII) and not re.search(r"\biff\b", it, re.ASCII) for it in items
        ):
            return m.start()
    return None


def _refusal_in(name: str, text: str, hand: bool) -> Refusal | None:
    _, scan, problem = lex(text)

    def refuse(what: str, pos: int, code: str = "sim_synth", src: str = scan) -> Refusal:
        return Refusal(code, what, name, _line(src, pos))

    if problem is not None:
        return refuse(problem[1], problem[0])
    for m in _DIRECTIVE.finditer(scan):
        word = m.group(1)
        if word == "include":
            return refuse("`include", m.start(), "include")
        if word is None or m.group(0) == "``" or word not in ALLOWED_DIRECTIVES:
            return refuse(f"the macro or directive `{word or ''}", m.start())
    cases = _case_constructs(scan)
    for m in _WORD.finditer(scan):
        word = m.group(0)
        if word in KEYWORDS or word in TWO_STATE:
            kind = "two-state type" if word in TWO_STATE else "keyword"
            return refuse(f"the {kind} `{word}`", m.start())
        if word == "enum":
            base = re.match(r"\s*(logic|reg|integer)\b", scan[m.end() :], re.ASCII)
            if base is None:
                return refuse("an `enum` without a logic, reg or integer base type", m.start())
        if (
            hand
            and word in ("function", "task")
            and not re.match(r"\s*automatic\b", scan[m.end() :], re.ASCII)
        ):
            return refuse(f"a `{word}` that is not `automatic`", m.start())
    for task in SYSTEM_TASKS:
        at = scan.find(task)
        if at >= 0:
            return refuse(f"`{task}`", at)
    for op in ("===", "!==", "==?", "!=?"):
        at = scan.find(op)
        if at >= 0:
            return refuse(f"the case-equality operator `{op}`", at)
    for m in _UNBASED.finditer(scan):
        if m.group(0)[1] in "xX" or not _wildcard_ok(scan, cases, m.start(), m.end()):
            return refuse(f"the literal `{m.group(0)}` (an x or z value)", m.start())
    for m in _BASED.finditer(scan):
        digits = m.group(2)
        shown = m.group(0).strip()
        if re.search(r"[xX]", digits):
            return refuse(f"a literal with an x digit (`{shown}`)", m.start())
        if re.search(r"[zZ?]", digits) and not _wildcard_ok(scan, cases, m.start(), m.end()):
            return refuse(f"a literal with a z or ? digit (`{shown}`)", m.start())
    for m in re.finditer(r"#", scan):
        if not _parameter_list(scan, m.start()):
            return refuse("a delay (`#`)", m.start())
    for m in _LINT_OFF.finditer(text):  # a comment: look at the original text
        words = re.findall(r"[-\w]+", m.group(1), re.ASCII)
        code = next((w for w in words if not w.startswith("-")), "")
        if code == "" or code.upper() in LINT_CODES_NOT_TO_HIDE:
            return refuse(f"`verilator lint_off {code}`".replace(" `", "`"), m.start(), src=text)
    if hand:
        event = _event_controls(scan)
        if event is not None:
            return refuse("an event control that is not `@*`, `@(*)` or edges", event)
    return None


def refusal(
    files: list[tuple[str, str]], hand_written: Collection[str] | None = None
) -> Refusal | None:
    """The first construct in the files (read in this order) that the check cannot vouch for.

    `hand_written`: names of the files a person wrote (None: all of them)."""
    for name, text in files:
        found = _refusal_in(name, text, hand_written is None or name in hand_written)
        if found is not None:
            return found
    return None


_ADVICE = (
    ("macro or directive", "Use a `localparam` or `parameter` instead: a macro can build text the "
     "check never sees."),
    ("two-state", "Use `reg` or `logic` and reset it with rst_n: a two-state type starts at 0 in "
     "a simulator, which the check cannot see."),
    ("enum", "Write `enum logic [1:0] {...}`: without a base type it is a two-state int."),
    ("keyword", "Use a plain `case` or `if`: synthesis assumes things a simulator does not."),
    ("delay", "Remove it: a simulator waits, synthesis ignores it."),
    ("case-equality", "Use `==` or `!=`: the check cannot model what `===` does with x."),
    ("literal", "Remove the x or z (an x is a don't-care in the gates; use a plain value): a "
     "simulator and the gates treat it differently."),
    ("lint_off", "Remove it and fix the lint warning instead: the lint step catches races and x "
     "in a case that no other check sees."),
    ("event control", "Write `always @(*)`: a simulator does not rerun the block for a signal "
     "missing from the list, and the gates do not care about the list."),
    ("automatic", "Write `function automatic`: a simulator keeps the local variables of a plain "
     "function between calls."),
    ("$", "Remove it: it reads a file or an option the check does not have."),
)  # fmt: skip


def plain_message(found: Refusal) -> str:
    """What to tell the agent, in plain English."""
    if found.code == "include":
        return (
            f"the RTL ({found.file}, line {found.line}) uses `include. The check only reads "
            "project.v and the design file, so it cannot see what an included file does. Put "
            "the code in the design file."
        )
    hint = next((v for k, v in _ADVICE if k in found.what), "Remove it.")
    return (
        f"the RTL ({found.file}, line {found.line}) uses {found.what}, which a simulator and "
        f"the synthesis read differently, so the check cannot vouch for it. {hint}"
    )
