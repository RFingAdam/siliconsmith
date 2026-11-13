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

* `function` or `task` without `automatic` (any front-end): a simulator keeps the local variables
  of a plain function between calls.

Only in a core file that lists no level signals itself (strict: Verilog and Amaranth; never
`project.v`, which the tool writes):

* every event control that is not `@*`, `@(*)` or only `posedge`/`negedge` items (`always @(a or
  b)`): a simulator does not rerun the block for a signal missing from the list, the gates do not
  care about the list.

A MyHDL core (`myhdl_core`) may be arbitrary text (its top can return any object with a
`convert()`), so its level-sensitive blocks are judged by a positive grammar, not by token hunting:
the block must have exactly the shape `@always_comb` writes and list every signal it reads (see
`_Block`); anything else is refused and the advice is `@always_comb`.

In every file, a CR that no LF follows (Icarus ends a `//` comment there, the others do not) and
an attribute whose string holds `*)` (Verilator ends the attribute there) are refused.
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
    label: str = ""  # a MyHDL block label (`begin: label`), to name the agent's function


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

    # Icarus ends a `//` comment at a CR that no LF follows; Yosys and Verilator do not. Where a
    # line ends is not settled for such a file, so it is refused wherever the CR is (CRLF is fine).
    lone_cr = re.search(r"\r(?!\n)", text)
    if lone_cr is not None:
        note(lone_cr.start(), "a carriage return that no line feed follows")

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
                    if end is not None and "*)" in text[k:end]:
                        # Verilator does not skip strings inside an attribute: it ends the
                        # attribute at this `*)`, and what follows is code there but a string here.
                        note(i, "an attribute (* ... *) with `*)` inside a string")
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


_EDGE_ITEM = re.compile(r"\s*(posedge|negedge)\s+\S", re.ASCII)


def _split_items(inner: str) -> list[str]:
    """The items of an event list: split at `or` and `,` outside brackets."""
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
    return items


def _is_edge(item: str) -> bool:
    return _EDGE_ITEM.match(item) is not None and re.search(r"\biff\b", item, re.ASCII) is None


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
        if not all(_is_edge(it) for it in _split_items(inner)):
            return m.start()
    return None


# -- MyHDL: a level-sensitive block must be the plain shape @always_comb writes -------------------
#
# MyHDL writes `@always_comb` as `always @(a, b) begin: label ... end` with every signal the block
# reads in the list, so a simulator reruns it exactly when the gates would change. `@always(a)` (or
# any spelling of it, or a `convert()` that writes the text by hand) gives a list somebody chose: a
# signal the block reads but does not list changes the output in the gates and not in a simulator.
# This is judged on the emitted text, which does not care how the Python was spelled.
#
# The judge is a small recursive-descent parser for exactly the shape MyHDL writes (the grammar is
# in `_Block`), not a hunt for bad tokens: whatever it does not expect is refused (fail closed).
#
#   level_block := 'always' '@' '(' item {',' item} ')' 'begin' ':' LABEL {decl} {stmt} 'end'
#   item        := ID | ID '[' NUMBER ']'     (the second only for a module-level array)
#   decl        := ('reg' ['signed'] [range] | 'integer') ID ';'          (top of the block only)
#   stmt        := target '=' expr ';' | if / else if / else | case / casez | for
#   block       := 'begin' {stmt} 'end' | stmt                            (never a named block)
#   target      := ID ['[' expr ']' | '[' expr ':' expr ']']

_TOKEN = re.compile(
    r"""\s+
    |(?P<num>(?:\d[\d_]*\s*)?'\s*[sS]?\s*[bBoOdDhH]\s*[0-9a-fA-FxXzZ?_]+|\d[\w.]*)
    |(?P<sys>\$[\w$]*)
    |(?P<id>[A-Za-z_][\w$]*)
    |(?P<op>===|!==|==|!=|<=|>=|<<<|>>>|<<|>>|&&|\|\||~&|~\||~\^|\^~|\*\*|\+:|-:|.)
    """,
    re.VERBOSE | re.ASCII | re.DOTALL,
)
_ITEM = re.compile(r"([A-Za-z_][\w$]*)(?:\s*\[\s*(\d+)\s*\])?", re.ASCII)
_EXPR_OPS = frozenset(
    {"+", "-", "*", "/", "%", "&", "|", "^", "~", "!", "<", ">", "?", ":", "==", "!=", "<=", ">="}
    | {"<<", ">>", "<<<", ">>>", "&&", "||", "~&", "~|", "~^", "^~", "**", ",", "(", ")", "[", "]"}
    | {"{", "}"}
)
_CLOSE = {"(": ")", "[": "]", "{": "}"}
# openers that make a module-level `reg` a declaration of the module, not of a block or function
_SCOPE_OPEN = {
    "begin": "end", "fork": "join", "case": "endcase", "casez": "endcase", "casex": "endcase",
    "function": "endfunction", "task": "endtask",
}  # fmt: skip

Token = tuple[str, str, int]  # (kind: num|sys|id|op, text, position)


def _tokens(text: str) -> list[Token]:
    return [(m.lastgroup, m.group(0), m.start()) for m in _TOKEN.finditer(text) if m.lastgroup]


class _Refuse(Exception):
    """The parser met something that is not the shape MyHDL writes."""

    def __init__(self, pos: int, what: str) -> None:
        super().__init__(what)
        self.pos, self.what = pos, what


def _module_arrays(tokens: list[Token]) -> dict[str, int]:
    """The module-level unpacked arrays `reg [7:0] mem [0:4-1];` as `{name: number of words}`.

    Only a `reg` outside every block counts, and only the exact shape; a part select such as
    `v[0:3]` in an expression is not a declaration."""
    found: dict[str, int] = {}
    stack: list[str] = []
    n = len(tokens)
    for i, (kind, text, _) in enumerate(tokens):
        if kind != "id":
            continue
        if text in _SCOPE_OPEN:
            stack.append(_SCOPE_OPEN[text])
        elif stack and text == stack[-1]:
            stack.pop()
        elif not stack and text == "reg":
            j = i + 1
            if j < n and tokens[j][1] == "signed":
                j += 1
            if j < n and tokens[j][1] == "[":
                j = _group_end(tokens, j, "[", "]") or n
            shape = [t[1] for t in tokens[j + 1 : j + 4]] if j < n else []
            if j >= n or tokens[j][0] != "id" or tokens[j][1] in RESERVED or shape[:1] != ["["]:
                continue
            close = _group_end(tokens, j + 1, "[", "]")
            inner = [t[1] for t in tokens[j + 2 : (close or n) - 1]]
            if close is None or close >= n or tokens[close][1] != ";" or inner[:2] != ["0", ":"]:
                continue
            rest = inner[2:]
            if len(rest) == 1 and rest[0].isdigit():
                found[tokens[j][1]] = int(rest[0]) + 1
            elif len(rest) == 3 and rest[0].isdigit() and rest[1] == "-" and rest[2].isdigit():
                found[tokens[j][1]] = int(rest[0]) - int(rest[2]) + 1
    return found


def _group_end(tokens: list[Token], i: int, open_c: str, close_c: str) -> int | None:
    """Index just past the group whose opening token is `tokens[i]`, or None."""
    depth = 0
    for j in range(i, len(tokens)):
        if tokens[j][0] == "op":
            depth += tokens[j][1] == open_c
            depth -= tokens[j][1] == close_c
            if depth == 0:
                return j + 1
    return None


class _Block:
    """Parse one level block (from its `begin`) and collect what it reads.

    Semantics checked while parsing: a local (`reg`/`integer` at the top of the block) is written
    before it is read (a named block keeps its locals between runs, like a static variable): its
    first occurrence is a top-level `name = expr;` or the variable of a `for` header, which it may
    then be read in only. The reads of everything else are returned for the completeness check."""

    def __init__(self, tokens: list[Token], i: int) -> None:
        self.t = tokens
        self.i = i
        self.locals: set[str] = set()
        self.state: dict[str, str] = {}  # local -> "full" (written) or "loop" (a for variable)
        self.loops: list[str] = []
        self.reads: list[tuple[str, str | None, int]] = []  # (name, index or None, position)
        self.label = ""

    # -- token helpers
    def peek(self, k: int = 0) -> str:
        return self.t[self.i + k][1] if self.i + k < len(self.t) else ""

    def pos(self) -> int:
        return self.t[min(self.i, len(self.t) - 1)][2] if self.t else 0

    def fail(self, why: str) -> _Refuse:
        return _Refuse(
            self.pos(),
            f"a level-sensitive block that is not the plain shape @always_comb writes ({why})",
        )

    def take(self, text: str) -> None:
        if self.peek() != text:
            raise self.fail(f"expected `{text}`, found `{self.peek() or 'the end'}`")
        self.i += 1

    def name(self) -> str:
        kind = self.t[self.i][0] if self.i < len(self.t) else ""
        text = self.peek()
        if kind != "id" or text in RESERVED:
            raise self.fail(f"`{text or 'the end'}` is not a plain name")
        if self.peek(1) == "(":
            raise self.fail(f"a call `{text}(...)`")
        self.i += 1
        return text

    # -- grammar
    def block(self) -> None:
        self.take("begin")
        if self.peek() == ":":
            raise self.fail("a named block inside the block")
        while self.peek() != "end":
            self.stmt(False)
        self.take("end")

    def body(self) -> None:
        """`block := 'begin' {stmt} 'end' | stmt`."""
        if self.peek() == "begin":
            self.block()
        else:
            self.stmt(False)

    def level_block(self) -> str:
        self.take("begin")
        self.take(":")
        self.label = self.name()
        while self.peek() in ("reg", "integer"):
            self.decl()
        while self.peek() != "end":
            self.stmt(True)
        self.take("end")
        return self.label

    def decl(self) -> None:
        word = self.peek()
        self.i += 1
        if word == "reg":
            if self.peek() == "signed":
                self.i += 1
            if self.peek() == "[":
                end = _group_end(self.t, self.i, "[", "]")
                if end is None or any(k not in ("num", "op") for k, _, _ in self.t[self.i : end]):
                    raise self.fail("a range that is not plain numbers")
                self.i = end
        name = self.name()
        if name in self.locals or name == self.label:
            raise self.fail(f"`{name}` is declared twice")
        self.take(";")
        self.locals.add(name)

    def stmt(self, top: bool) -> None:
        word = self.peek()
        if word == "if":
            self.i += 1
            self.cond()
            self.body()
            while self.peek() == "else":
                self.i += 1
                if self.peek() != "if":
                    self.body()
                    break
                self.i += 1
                self.cond()
                self.body()
        elif word in ("case", "casez"):
            self.case()
        elif word == "for":
            self.for_loop()
        elif self.i < len(self.t) and self.t[self.i][0] == "id" and word not in RESERVED:
            self.assign(top)
        else:
            raise self.fail(f"`{word or 'the end'}` is not a statement MyHDL writes")

    def cond(self) -> None:
        self.take("(")
        self.expr({")"})
        self.take(")")

    def case(self) -> None:
        self.i += 1
        self.cond()
        while self.peek() not in ("endcase", "default"):
            while True:
                if self.i >= len(self.t) or self.t[self.i][0] != "num":
                    raise self.fail("a case item that is not a number")
                self.i += 1
                if self.peek() != ",":
                    break
                self.i += 1
            self.take(":")
            self.body()
        if self.peek() == "default":
            self.i += 1
            self.take(":")
            self.body()
        self.take("endcase")

    def for_loop(self) -> None:
        self.i += 1
        self.take("(")
        var = self.name()
        self.take("=")
        self.expr({";"})
        self.take(";")
        if var in self.locals and var not in self.state:
            self.state[var] = "loop"
        self.loops.append(var)
        self.expr({";"})
        self.take(";")
        if self.name() != var:
            raise self.fail("a for loop that steps another variable")
        self.take("=")
        self.expr({")"})
        self.take(")")
        self.body()
        self.loops.pop()

    def assign(self, top: bool) -> None:
        start = self.pos()
        target = self.name()
        indexed = self.peek() == "["
        if indexed:
            self.i += 1
            self.expr({"]", ":"})
            if self.peek() == ":":
                self.i += 1
                self.expr({"]"})
            self.take("]")
        if self.peek() == "<=":
            raise self.fail("a non-blocking assignment")
        self.take("=")
        self.expr({";"})
        self.take(";")
        if target in self.locals:
            ok = self.state.get(target) == "full" or target in self.loops
            if top and not indexed:
                self.state[target] = "full"
            elif not ok:
                raise _Refuse(
                    start,
                    f"a level-sensitive block that writes its local `{target}` only in part or "
                    "conditionally before it is first written",
                )

    def expr(self, stops: set[str]) -> None:
        """Balanced tokens of names, numbers, operators, `?:`, `()`, `[]`, `{}` and
        `$signed(` / `$unsigned(`; ends at a token of `stops` outside every bracket."""
        depth: list[str] = []
        ternary = [0]  # open `?` per bracket level
        begun = False
        while self.i < len(self.t):
            kind, text, pos = self.t[self.i]
            if not depth and text in stops and not (text == ":" and ternary[0]):
                break
            if kind == "num":
                pass
            elif kind == "sys":
                if text not in ("$signed", "$unsigned") or self.peek(1) != "(":
                    raise self.fail(f"the system function `{text}`")
            elif kind == "id":
                if text in RESERVED:
                    raise self.fail(f"the reserved word `{text}` in an expression")
                if self.peek(1) == "(":
                    raise self.fail(f"a call `{text}(...)`")
                self.use(text, pos)
            elif text in _CLOSE:
                depth.append(_CLOSE[text])
                ternary.append(0)
            elif text in _CLOSE.values():
                if not depth or depth.pop() != text:
                    raise self.fail(f"an unmatched `{text}`")
                ternary.pop()
            elif text not in _EXPR_OPS:
                raise self.fail(f"`{text}` in an expression")
            elif text == "?":
                ternary[-1] += 1
            elif text == ":" and ternary[-1]:
                ternary[-1] -= 1
            elif text == "," and not depth:
                raise self.fail("a `,` outside brackets")
            begun = True
            self.i += 1
        if depth or not begun:
            raise self.fail("an expression that is empty or not closed")

    def use(self, name: str, pos: int) -> None:
        """A read of `name` (its index, if it is a single decimal number, is kept)."""
        if name in self.locals:
            if not (self.state.get(name) == "full" or name in self.loops):
                raise _Refuse(
                    pos,
                    f"a level-sensitive block that reads its local `{name}` before it is "
                    "written (a named block keeps the value between runs)",
                )
            return
        index: str | None = None
        if self.peek(1) == "[":
            end = _group_end(self.t, self.i + 1, "[", "]")
            inner = self.t[self.i + 2 : (end or 0) - 1]
            index = inner[0][1] if len(inner) == 1 and inner[0][1].isdigit() else ""
        self.reads.append((name, index, pos))


def _needs(
    reads: list[tuple[str, str | None, int]],
    plain: set[str],
    words: set[tuple[str, int]],
    arrays: dict[str, int],
) -> tuple[int, str] | None:
    """The first read that the event list does not cover: `(position, name)`.

    A plain vector must be listed by name (a bit item is not enough). A read of an array word
    `mem[3]` needs that word (or all of them); any other read of an array needs every word."""
    for name, index, pos in reads:
        if name in arrays:
            every = all((name, w) in words for w in range(arrays[name]))
            if every or (index and (name, int(index)) in words):
                continue
        elif name in plain:
            continue
        return pos, name
    return None


def _level_block(
    tokens: list[Token], at: int, items: list[str], arrays: dict[str, int]
) -> tuple[int, str, str] | None:
    """Judge the block that follows the event control ending at `scan[at]`: `(position, what,
    label)` of the first problem, or None."""
    plain: set[str] = set()
    words: set[tuple[str, int]] = set()
    for item in items:
        m = _ITEM.fullmatch(item.strip())
        if m is None or m.group(1) in RESERVED:
            return at, f"a level list item that is not a plain signal name (`{item.strip()}`)", ""
        if m.group(2) is None:
            plain.add(m.group(1))
        elif m.group(1) in arrays and int(m.group(2)) < arrays[m.group(1)]:
            words.add((m.group(1), int(m.group(2))))  # how MyHDL lists a list of signals
        else:
            return at, f"a level list item `{item.strip()}` that is not a word of an array", ""
    first = next((i for i, t in enumerate(tokens) if t[2] >= at), len(tokens))
    parser = _Block(tokens, first)
    try:
        label = parser.level_block()
    except _Refuse as why:
        return why.pos, why.what, parser.label
    missing = _needs(parser.reads, plain, words, arrays)
    if missing is not None:
        return (
            missing[0],
            f"a level-sensitive block that reads `{missing[1]}` without listing it",
            label,
        )
    return None


def _myhdl_level_blocks(scan: str) -> tuple[int, str, str] | None:
    """`(position, what, label)` of the first level-sensitive event control in a MyHDL core that
    is not the plain shape `@always_comb` writes (see `_Block`) with every signal it reads in the
    list. An escaped identifier anywhere in the core is refused as well."""
    if "\\" in scan:  # a string is blanked in `scan`, so this is an escaped identifier
        return scan.index("\\"), "a level-sensitive core with an escaped identifier", ""
    tokens: list[Token] | None = None
    arrays: dict[str, int] = {}
    for m in re.finditer(r"@", scan):
        k = _skip_blank(scan, m.end())
        if scan[k : k + 1] == "*":
            continue
        end = _balanced(scan, k, "(", ")") if scan[k : k + 1] == "(" else None
        if end is None:
            return m.start(), "a level-sensitive event control that cannot be read", ""
        inner = scan[k + 1 : end - 1].strip()
        if inner == "*":
            continue
        items = _split_items(inner)
        edges = [_is_edge(it) for it in items]
        if all(edges):
            continue
        if any(edges):
            return m.start(), "a level-sensitive event list that mixes edges and level signals", ""
        if _word_before(scan, m.start())[0] != "always":
            return m.start(), "a level-sensitive event control that is not an `always` block", ""
        if tokens is None:
            tokens = _tokens(scan)
            arrays = _module_arrays(tokens)
        found = _level_block(tokens, end, items, arrays)
        if found is not None:
            return found
    return None


def _refusal_in(name: str, text: str, hand: bool, myhdl: bool = False) -> Refusal | None:
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
        if word in ("function", "task") and not re.match(
            r"\s*automatic\b", scan[m.end() :], re.ASCII
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
    if myhdl:
        level = _myhdl_level_blocks(scan)
        if level is not None:
            found = refuse(level[1], level[0])
            return Refusal(found.code, found.what, found.file, found.line, level[2])
    return None


def refusal(
    files: list[tuple[str, str]],
    hand_written: Collection[str] | None = None,
    myhdl_core: Collection[str] = (),
) -> Refusal | None:
    """The first construct in the files (read in this order) that the check cannot vouch for.

    `hand_written`: names of the files judged by the strict event-list and function rules (None:
    all of them). `myhdl_core`: names of MyHDL cores, judged by the level-list rule instead."""
    for name, text in files:
        found = _refusal_in(
            name, text, hand_written is None or name in hand_written, name in myhdl_core
        )
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
    ("level-sensitive", "Use `@always_comb`: it writes the plain shape and lists every signal the "
     "block reads, so a simulator reruns the block exactly when the gates would change."),
    ("level list item", "Use `@always_comb`: it lists every signal the block reads, by name."),
    ("carriage return", "Save the file with LF or CRLF line ends: the tools disagree about where "
     "a line ends at a lone CR."),
    ("event control", "Write `always @(*)`: a simulator does not rerun the block for a signal "
     "missing from the list, and the gates do not care about the list."),
    ("automatic", "Write `function automatic`: a simulator keeps the local variables of a plain "
     "function between calls."),
    ("$", "Remove it: it reads a file or an option the check does not have."),
)  # fmt: skip


def plain_message(found: Refusal, where: str = "") -> str:
    """What to tell the agent, in plain English. `where`: more about the place, after the line."""
    if found.code == "include":
        return (
            f"the RTL ({found.file}, line {found.line}) uses `include. The check only reads "
            "project.v and the design file, so it cannot see what an included file does. Put "
            "the code in the design file."
        )
    hint = next((v for k, v in _ADVICE if k in found.what), "Remove it.")
    return (
        f"the RTL ({found.file}, line {found.line}{where}) uses {found.what}, which a simulator "
        f"and the synthesis read differently, so the check cannot vouch for it. {hint}"
    )
