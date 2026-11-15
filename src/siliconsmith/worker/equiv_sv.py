# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Prepare the RTL text for the equivalence check, and refuse what the check cannot vouch for.

## What the check promises (stdlib only, runs in the container)

PROVEN: the netlist implements the RTL as Yosys reads it, once comments, attributes and macros are
out of the way. In plain words: synthesis did not change your logic.

BEST EFFORT: Yosys and a simulator do not read every Verilog file the same way. `refusal()` spots
the common constructs where they differ and the check answers `not_proven` (with the construct
named) instead of a verdict. What it does NOT spot is left to gate-level simulation
(`run_tests` with `gl=true`):

* an index computed from a signal or a variable that goes outside the range (x in a simulator,
  which an `if` or `!` can turn into a defined 0 or 1; some value in the gates, because an x in
  the RTL is a don't-care), also a loop index passed into a function that selects with it (Yosys
  works such a call out without a warning), a division by a signal that is 0 (also inside a
  generate loop or module copy), and a function that reads one of its own locals before it
  writes it (a constant that comes out x, a constant index outside the range, a parameter value
  that the simulator and Verilator work out differently, a generate condition that can be x, a
  call with constant arguments of a function that selects at a computed index or divides, and a
  select outside the range that Yosys finds after inlining are refused by the checks on
  Verilator's tree and Icarus's and Yosys's elaborations in `equiv`);
* a constant expression that Yosys evaluates differently from a simulator: a size cast or an
  unbased `'1` inside a localparam, `$rtoi` in any constant expression (a localparam or a
  generate condition); only a parameter value on which the simulator and Verilator disagree is
  refused (an untyped `localparam` that overflows, `localparam K = 8'hff + 8'h01`, is: Icarus
  makes it 9'h100, Verilator 8'h00);
* anything else that depends on the order in which a simulator runs events.

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
  four-state base type (`enum logic [1:0] {...}` is fine): they start at 0 in a simulator. The
  one exception is the index of a `for` loop declared in its header (`for (int i = 0; ...)`,
  `_loop_indexes`): it belongs to the loop and the header sets it before every run;
* `===` and `!==`: the check models them differently from a simulator;
* `==?`, `!=?` and `inside` (wildcard comparisons);
* x digits in a based literal (`4'b1x0`, `'hx`) and the unbased `'x`; z or ? digits and `'z`
  anywhere but as the label of an item of a `casez` (innermost case is a `casez`, outside the
  header, at bracket depth 0, after `;` `,` `end` `endcase` or the header `)`, before `,` or `:`):
  there they are wildcards for both a simulator and synthesis;
* an unsized number of 2**31 or more (a plain decimal `2147483648` or a based number without a
  size, `'hFFFFFFFF`): Verilator reads `2147483648` as a negative 32-bit value, Yosys and Icarus
  as a wider positive one, so the checks judged on Verilator's tree (`equiv_tree`) could follow
  another branch than the proof (`if (2147483648 > 0)`); a size (`32'd2147483648`) is fine;
* every character outside printable ASCII, tab, CR and LF that is not in a comment (the tools
  disagree about where an escaped identifier ends and what counts as a blank);
* a back-tick or a double quote inside an escaped identifier (`\\v`__ICARUS__`, `\\M"`): Icarus
  expands a macro there and Yosys and Verilator do not, and Verilator and Icarus spell a double
  quote in a name differently, so the values the two give such a parameter are never compared;
* delay controls (`#`), except a parameter list in exactly two shapes:
  `module NAME #(` and `NAME #( ... ) INSTANCE [range] (`; neither name may be a reserved word;
* `$readmemh`, `$readmemb`, `$test$plusargs`, `$value$plusargs`;
* a `// verilator lint_off` comment with no code, or with a code that hides what the lint step
  catches (BLKSEQ, COMBDLY, CASEX, CASEWITHX, CASEINCOMPLETE, LATCH, MULTIDRIVEN, ...), also as
  any item of a comma list (`lint_off WIDTH,BLKSEQ`: Verilator turns off every code of it): the
  lint runs with every warning fatal, and that is what finds a blocking race or an x in a case
  item.

* `function` without `automatic` (any front-end): a simulator keeps the local variables of a plain
  function between calls; a function argument that is not an input (`output`, `inout` or `ref`
  inside a function: Icarus cannot compile one, and a call on one path of a statement writes it
  on that path only, which the order check cannot follow); a `function` that reads anything but
  its own arguments, locals, parameters and pure functions (a simulator's `@*` does not see it;
  judged on Verilator's syntax tree in `equiv_tree`, not here); every `task`; a hierarchical
  name inside a function (a dotted name that is not a member of an argument or local: `g.k`,
  also split over lines; `p.lo` of an argument or local `p` is fine unless `p` is also a block
  label, module, instance or call name of the file).

Only in a core file that lists no level signals itself (strict: Verilog and Amaranth; never
`project.v`, which the tool writes):

* every event control that is not `@*`, `@(*)` or only `posedge`/`negedge` items (`always @(a or
  b)`): a simulator does not rerun the block for a signal missing from the list, the gates do not
  care about the list.

A MyHDL core (`myhdl_core`) may be arbitrary text (its top can return any object with a
`convert()`), so it is judged by a positive grammar, not by token hunting: the core must be the
plain single module MyHDL writes (see `_Module`), its level-sensitive blocks exactly the shape
`@always_comb` writes, listing every signal they read and reading none before writing it (see
`_Block`); anything else is refused and the advice is `@always_comb`.

A combinational block that can read a signal before it writes it on some path through the block
is also refused for every front-end, on Verilator's syntax tree (`equiv_tree.early_read`), and
again by Verilator's own lint (`always_comb` copy, ALWCOMBORDER): see `equiv._tree_checks`,
`equiv._comb_order` and `always_comb_copy`.

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
            # round 20: Icarus expands a macro inside an escaped identifier (`\v`__ICARUS__`
            # is `\v1` there), Yosys and Verilator do not; and Verilator and Icarus spell a
            # double quote in a name differently, so the values of `\M"` are never compared
            odd = next((x for x in text[i:j] if x in '`"'), None)
            if odd is not None:
                note(
                    text.index(odd, i),
                    f"a {'back-tick' if odd == '`' else 'double quote'} inside an escaped "
                    "identifier (Icarus expands a macro there, Yosys and Verilator do not; "
                    "Verilator and Icarus spell a double quote in a name differently)",
                )
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


_ALWAYS_STAR = re.compile(r"(?<![\w$])always\s*@\s*(?:\*|\(\s*\*\s*\))", re.ASCII)


def always_comb_copy(text: str) -> str | None:
    """The cleaned RTL with every `always @*` and `always @(*)` written as `always_comb` (found
    in the tokenized text, so a comment or a string is never touched; line numbers do not move),
    or None if there is none. Verilator checks an `always_comb` for a signal that is driven after
    it is used (ALWCOMBORDER), which is a difference between a simulator (it does not rerun the
    block for the block's own write) and the gates; it says nothing about an `@*`."""
    cleaned, scan, _ = lex(text)
    out: list[str] = []
    last = 0
    for m in _ALWAYS_STAR.finditer(scan):
        out.append(cleaned[last : m.start()])
        glued = cleaned[m.end() : m.end() + 1] not in ("", " ", "\t", "\n", "\r")  # `@(*)begin`
        out.append("always_comb" + " " * glued + "\n" * m.group(0).count("\n"))
        last = m.end()
    if not out:
        return None
    out.append(cleaned[last:])
    return "".join(out)


_FUNCTION_BODY = re.compile(r"\bfunction\b.*?\bendfunction\b", re.S | re.ASCII)
_DOTTED = re.compile(r"(?<![\w$'])[A-Za-z_][\w$]*\s*\.\s*[A-Za-z_]", re.ASCII)
_DIRECTIVE = re.compile(r"``|`([A-Za-z_]\w*)?", re.ASCII)
_DECLARES = re.compile(
    r"(?:input|output|inout|ref|const|var|static|automatic|reg|logic|wire|integer|signed|"
    r"unsigned|bit|byte|shortint|int|longint|real|realtime|time)\b",
    re.ASCII,
)
# Names that open a scope a dotted name can reach into: a block label (`begin : g`, `g : begin`),
# a module, interface or program, an instance, an array of instances or a call (any word before
# a `(`, possibly after `[...]`: more than the instances, never fewer). A word right after a `.`
# (blanks allowed between) is not one: it is a named port connection (`.cfg(cfg)`) or the member
# of a dotted name (`g.rd(x)`, whose head `g` is what counts).
_CALL_OR_INSTANCE = re.compile(r"(?<![\w$'])([A-Za-z_][\w$]*)\s*(?:\[[^\]]*\]\s*)*\(", re.ASCII)
_SCOPES = (
    re.compile(r"\b(?:begin|fork)\s*:\s*([A-Za-z_][\w$]*)", re.ASCII),
    re.compile(r"(?<![\w$'])([A-Za-z_][\w$]*)\s*:\s*(?:begin|fork)\b", re.ASCII),
    re.compile(r"\b(?:module|macromodule|interface|program)\s+([A-Za-z_][\w$]*)", re.ASCII),
    _CALL_OR_INSTANCE,
)
_WORD = re.compile(r"(?<![\w$'])[A-Za-z_][\w$]*", re.ASCII)
_BASED = re.compile(
    r"(?<![\w$])(?:\d[\d_]*\s*)?'\s*[sS]?\s*([bBoOdDhH])\s*([0-9a-fA-FxXzZ?_]+)", re.ASCII
)
_UNBASED = re.compile(r"'[xXzZ](?![\w])", re.ASCII)
# a plain decimal number: not part of a word, a real (`1.5`, `2e9`) or the size of a based literal
_DECIMAL = re.compile(r"(?<![\w$'.])\d[\d_]*(?![\w$.]|\s*')", re.ASCII)
_RADIX = {"b": 2, "o": 8, "d": 10, "h": 16}
UNSIZED_MAX = (1 << 31) - 1  # the largest unsized number every tool reads the same way
ARGUMENT_DIRECTIONS = frozenset({"output", "inout", "ref"})
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


def _after_dot(scan: str, pos: int) -> bool:
    """True if the first character before `pos` that is not a blank is a `.`."""
    k = pos - 1
    while k >= 0 and scan[k] in ASCII_BLANKS:
        k -= 1
    return k >= 0 and scan[k] == "."


def _scope_names(scan: str) -> set[str]:
    """Every name in the file that may open a scope (`_SCOPES`): a dotted name whose head is one
    of them is never taken for a member of a function's argument or local."""
    return {
        m.group(1)
        for pattern in _SCOPES
        for m in pattern.finditer(scan)
        if not (pattern is _CALL_OR_INSTANCE and _after_dot(scan, m.start(1)))
    }


_FOR_HEADER = re.compile(r"(?<![\w$'])for\s*\(", re.ASCII)
_LOOP_DECLARATION = re.compile(
    r"(?:\s+(?:signed|unsigned)\b)?\s+[A-Za-z_][\w$]*\s*=(?!=)", re.ASCII
)  # after the type word: `[signed|unsigned] NAME =`


def _loop_indexes(scan: str) -> set[int]:
    """Where a two-state type word (`TWO_STATE`) is the type of a variable declared in the
    initialization of a `for` header (`for (int i = 0; ...)`, also `int unsigned i = 0` and a
    second `, int j = 7`): the start of each such word. Such a variable is automatic, belongs to
    the loop and is set by the header before every run of the loop, so it never shows the 0 a
    two-state variable starts at. Only a word at the top level of the header's first clause (up
    to its first `;`), right after the `(` or a `,`, and followed by a name and a single `=`
    counts: a cast (`int'(x)`) or any other use is not a declaration."""
    found: set[int] = set()
    for header in _FOR_HEADER.finditer(scan):
        depth, k, previous = 0, header.end(), "("
        while k < len(scan):
            c = scan[k]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break  # the header ended before a `;`: no initialization clause
                depth -= 1
            elif depth == 0 and c == ";":
                break
            elif depth == 0 and previous in "(," and (c.isalpha() or c == "_"):
                word = _WORD.match(scan, k)
                if (
                    word is not None
                    and word.group(0) in TWO_STATE
                    and _LOOP_DECLARATION.match(scan, word.end())
                ):
                    found.add(k)
                if word is not None:
                    k = word.end()
                    previous = "w"
                    continue
            if c not in ASCII_BLANKS:
                previous = c if depth == 0 else "x"
            k += 1
    return found


def _split_top(text: str, sep: str) -> list[str]:
    """`text` split at every `sep` outside brackets, parentheses and braces."""
    parts, depth, last = [], 0, 0
    for k, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == sep and depth == 0:
            parts.append(text[last:k])
            last = k + 1
    return [*parts, text[last:]]


def _declared(item: str) -> str | None:
    """The name a declaration item declares (`input pair_t p`, `logic [3:0] a [0:1] = 0`): the
    last word before any default value, after the unpacked ranges are cut off."""
    item = _split_top(item, "=")[0].rstrip()
    while item.endswith("]"):
        k = item.rfind("[")
        if k < 0:
            return None
        item = item[:k].rstrip()
    found = re.search(r"(?<![\w$'])([A-Za-z_][\w$]*)$", item, re.ASCII)
    return found.group(1) if found else None


def _port_list(body: str) -> int | None:
    """Where the port list of a function header starts: the first `(` outside a range (`[...]`,
    which may hold `$clog2(N)`) before the `;` that ends the header; None for a header without one
    (`function automatic [7:0] f;` with `input` statements)."""
    depth = 0
    for k, c in enumerate(body):
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
        elif depth == 0 and c == ";":
            return None
        elif depth == 0 and c == "(":
            return k
    return None


def _function_names(body: str) -> set[str]:
    """The arguments and the locals of one function (`function ... endfunction`, tokenized):
    the items of its port list, and the names of every statement of its body that starts with a
    declaration word (`input`, `logic`, `integer`, ...) or with a type and a name (`pair_t q;`).
    A local declared deeper (inside an `if`) is not found: its dotted names are refused."""
    names: set[str] = set()
    rest = body
    start = _port_list(body)
    head_end = _balanced(body, start, "(", ")") if start is not None else None
    if start is not None and head_end is not None:
        ports = body[start + 1 : head_end - 1]
        names |= {n for item in _split_top(ports, ",") if (n := _declared(item)) is not None}
        rest = body[head_end:]
    statements = _split_top(rest, ";")[1:]  # the first one ends the function header
    for statement in statements:
        statement = re.sub(r"^\s*(?:begin\b(?:\s*:\s*[A-Za-z_][\w$]*)?\s*)*", "", statement)
        words = re.match(r"([A-Za-z_][\w$]*)\s*(?:\[[^\]]*\]\s*)*([A-Za-z_][\w$]*)?", statement)
        if words is None:
            continue
        typed = words.group(2) is not None and words.group(1) not in RESERVED
        if _DECLARES.fullmatch(words.group(1)) or typed:
            names |= {
                n for item in _split_top(statement, ",") if (n := _declared(item)) is not None
            }
    return names


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
_EXPR_OPS = frozenset(
    {"+", "-", "*", "/", "%", "&", "|", "^", "~", "!", "<", ">", "?", ":", "==", "!=", "<=", ">="}
    | {"<<", ">>", "<<<", ">>>", "&&", "||", "~&", "~|", "~^", "^~", "**", ",", "(", ")", "[", "]"}
    | {"{", "}"}
)
_CLOSE = {"(": ")", "[": "]", "{": "}"}
Token = tuple[str, str, int]  # (kind: num|sys|id|op, text, position)


def _tokens(text: str) -> list[Token]:
    return [(m.lastgroup, m.group(0), m.start()) for m in _TOKEN.finditer(text) if m.lastgroup]


class _Refuse(Exception):
    """The parser met something that is not the shape MyHDL writes."""

    def __init__(self, pos: int, what: str, label: str = "") -> None:
        super().__init__(what)
        self.pos, self.what, self.label = pos, what, label


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


class _Toks:
    """A cursor over the tokens with the helpers both parsers share."""

    WHAT = ""  # what is refused, for the message: "<WHAT> (why)"

    def __init__(self, tokens: list[Token], i: int) -> None:
        self.t = tokens
        self.i = i

    def peek(self, k: int = 0) -> str:
        return self.t[self.i + k][1] if self.i + k < len(self.t) else ""

    def pos(self) -> int:
        return self.t[min(self.i, len(self.t) - 1)][2] if self.t else 0

    def fail(self, why: str) -> _Refuse:
        return _Refuse(self.pos(), f"{self.WHAT} ({why})")

    def take(self, text: str) -> None:
        if self.peek() != text:
            raise self.fail(f"expected `{text}`, found `{self.peek() or 'the end'}`")
        self.i += 1

    def name(self, call_ok: bool = False) -> str:
        kind = self.t[self.i][0] if self.i < len(self.t) else ""
        text = self.peek()
        if kind != "id" or text in RESERVED:
            raise self.fail(f"`{text or 'the end'}` is not a plain name")
        if self.peek(1) == "(" and not call_ok:
            raise self.fail(f"a call `{text}(...)`")
        self.i += 1
        return text

    def use(self, name: str, pos: int) -> None:
        """A read of `name` in an expression (the block parser keeps track of them)."""

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


class _Block(_Toks):
    """Parse one level block (from its `begin`) and collect what it reads.

    Semantics checked while parsing:
    * a local (`reg`/`integer` at the top of the block) is written before it is read (a named
      block keeps its locals between runs, like a static variable): its first occurrence is a
      top-level `name = expr;` or the variable of a `for` header, which it may then be read in only;
    * a signal of the module is not read before the block writes it: a simulator does not rerun
      the block for its own write, so `y = t; t = cnt;` gives y the OLD t while the gates give the
      new one. A read is fine if the name was written before it at the top level of the block;
      otherwise any later write to that name, anywhere, refuses the block (conservative).
    The reads of everything else are returned for the completeness check."""

    WHAT = "a level-sensitive block that is not the plain shape @always_comb writes"

    def __init__(self, tokens: list[Token], i: int) -> None:
        super().__init__(tokens, i)
        self.locals: set[str] = set()
        self.state: dict[str, str] = {}  # local -> "full" (written) or "loop" (a for variable)
        self.loops: list[str] = []
        self.written: set[str] = set()  # module signals written at the top level so far
        self.pending: dict[str, int] = {}  # module signals read, not yet written: first read
        # per active for loop: (names read, names written, names fully written before the loop)
        self.frames: list[tuple[dict[str, int], set[str], set[str]]] = []
        self.reads: list[tuple[str, str | None, int]] = []  # (name, index or None, position)
        self.label = ""

    def write(self, name: str, top: bool, indexed: bool) -> None:
        """A write to the module signal `name` (a local has its own rule)."""
        if name in self.pending:
            raise _Refuse(
                self.pending[name],
                f"a level-sensitive block that reads `{name}` before it writes it (a simulator "
                "does not rerun the block for its own write; write each signal before you read it)",
            )
        for _, writes, _ in self.frames:
            writes.add(name)
        if top and not indexed:
            self.written.add(name)

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
            self.for_loop(top)
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

    def for_loop(self, top: bool) -> None:
        self.i += 1
        self.take("(")
        var = self.name()
        self.take("=")
        self.expr({";"})
        self.take(";")
        if var in self.locals:
            if var not in self.state:
                self.state[var] = "loop"
        else:
            self.write(var, top, False)  # the start of a for loop is an unconditional write
        self.loops.append(var)
        self.frames.append(({}, set(), set(self.written)))
        self.expr({";"})
        self.take(";")
        if self.name() != var:
            raise self.fail("a for loop that steps another variable")
        self.take("=")
        self.expr({")"})
        self.take(")")
        if var not in self.locals:
            self.write(var, False, False)
        self.body()
        reads, writes, before = self.frames.pop()
        for name, pos in reads.items():
            if name in writes and name not in before and name not in self.loops:
                raise _Refuse(
                    pos,
                    f"a level-sensitive block that both reads and writes `{name}` in a for loop "
                    "(an iteration reads what another one writes, and a simulator does not rerun "
                    "the block for its own write; write it fully before the loop)",
                )
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
        else:
            self.write(target, top, indexed)

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
        for reads, _, _ in self.frames:
            reads.setdefault(name, pos)
        if name not in self.written and name not in self.loops:
            self.pending.setdefault(name, pos)
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


_DECL_KINDS = frozenset(
    {"input", "output", "inout", "reg", "wire", "integer", "localparam", "parameter"}
)
_OPENERS = {
    "begin": "end",
    "fork": "join",
    "case": "endcase",
    "casez": "endcase",
    "casex": "endcase",
}
_CLOSERS = frozenset({"end", "join", "join_any", "join_none", "endcase"})


class _Module(_Toks):
    """Parse a whole MyHDL core: exactly one plain module, nothing else.

        core    := 'module' ID '(' ports ')' ';' { item } 'endmodule'
        item    := decl | 'assign' target '=' expr ';' | always | 'initial' body
        decl    := ('input'|'output'|'inout') ['reg'|'wire'] | 'reg' | 'wire' | 'integer'
                   | 'localparam' | 'parameter'        then ['signed'] [range] ID {',' ID} ';'
                   (a name may carry `= expr`; `reg` may carry an unpacked array `[0:K]`)
        always  := 'always' '@' ( '*' | '(' '*' ')' | '(' edges ')' ) body        (not judged)
                 | 'always' '@' '(' items ')' 'begin' ... 'end'                     (`_Block`)

    An unpacked array is `reg [..] ID [0:K];` (K a decimal literal) or `[0:N-M]` with N-M >= 0,
    so it has at least one word; a name that is an array is declared nowhere else. A `body` is a
    `begin ... end` with its `case`/`fork` nesting matched, and holds no `@` or `wait`.
    Everything else at module level (generate, genvar, an instance, a second module, a bare
    `begin`, `if`, `for`, `case`, `function`, ...) is refused: it could change what a name means
    for a level block, and MyHDL writes none of it. MyHDL writes the port names in the header and
    the directions below it; an ANSI header is read too."""

    WHAT = "a MyHDL core that is not the plain single module MyHDL writes"

    def __init__(self, tokens: list[Token]) -> None:
        super().__init__(tokens, 0)
        self.arrays: dict[str, int] = {}
        self.names: set[str] = set()
        # per level block: (items as token lists, reads, label, position of its event control)
        self.levels: list[tuple[list[list[Token]], list[tuple[str, str | None, int]], str, int]]
        self.levels = []

    def parse(self) -> None:
        self.take("module")
        self.name(call_ok=True)  # the port list follows
        self.header()
        self.take(";")
        while self.peek() != "endmodule":
            if self.i >= len(self.t):
                raise self.fail("the module is never closed")
            self.item()
        self.take("endmodule")
        if self.i < len(self.t):
            raise self.fail(f"`{self.peek()}` after the module (a second module or other text)")

    def header(self) -> None:
        if self.peek() != "(":
            raise self.fail("no port list")
        end = _group_end(self.t, self.i, "(", ")")
        if end is None:
            raise self.fail("a port list that is not closed")
        self.i += 1
        while self.i < end - 1:
            kind, text, _ = self.t[self.i]
            if kind == "id" and text not in RESERVED:
                self.names.add(text)
                self.i += 1
            elif (
                text in ("input", "output", "inout", "reg", "wire", "signed", ",", "[", "]")
                or kind == "num"
            ):
                self.i += 1
            elif text in ("+", "-", ":", "*", "/", "(", ")"):
                self.i += 1  # inside a range
            else:
                raise self.fail(f"`{text}` in the port list")
        self.i = end

    def item(self) -> None:
        word = self.peek()
        if word in _DECL_KINDS:
            self.decl()
        elif word == "assign":
            self.i += 1
            self.name()
            if self.peek() == "[":
                self.i += 1
                self.expr({"]", ":"})
                if self.peek() == ":":
                    self.i += 1
                    self.expr({"]"})
                self.take("]")
            self.take("=")
            self.expr({";"})
            self.take(";")
        elif word == "always":
            self.always()
        elif word == "initial":
            self.i += 1
            self.skip_body()
        else:
            raise self.fail(f"`{word}` at module level")

    def decl(self) -> None:
        kind = self.peek()
        self.i += 1
        if kind in ("input", "output", "inout") and self.peek() in ("reg", "wire"):
            self.i += 1
        if self.peek() == "signed":
            self.i += 1
        if self.peek() == "[":
            end = _group_end(self.t, self.i, "[", "]")
            if end is None:
                raise self.fail("a range that is not closed")
            self.i = end
        while True:
            name = self.name()
            if self.peek() == "[":
                self.array(name, kind)
            elif name in self.arrays:
                raise self.fail(f"`{name}` is an array and is declared twice")
            elif self.peek() == "=":
                self.i += 1
                self.expr({",", ";"})
            self.names.add(name)
            if self.peek() != ",":
                break
            self.i += 1
        self.take(";")

    def array(self, name: str, kind: str) -> None:
        """`[0:K]` or `[0:N-M]` after a `reg` name: record the number of words (at least 1)."""
        if kind != "reg" or name in self.names:
            raise self.fail(f"`{name}` is an array that is not a plain `reg` declared once")
        end = _group_end(self.t, self.i, "[", "]")
        inner = [t[1] for t in self.t[self.i + 1 : (end or 0) - 1]]
        if inner[:2] != ["0", ":"] or end is None:
            raise self.fail(f"the array `{name}` is not `[0:K]`")
        rest = inner[2:]
        if len(rest) == 1 and rest[0].isdigit():
            words = int(rest[0]) + 1
        elif len(rest) == 3 and rest[0].isdigit() and rest[1] == "-" and rest[2].isdigit():
            words = int(rest[0]) - int(rest[2]) + 1
        else:
            raise self.fail(f"the array `{name}` is not `[0:K]` or `[0:N-M]` with plain numbers")
        if words < 1:
            raise self.fail(f"the array `{name}` has no word")
        self.arrays[name] = words
        self.i = end
        if self.peek() in ("=", "["):
            raise self.fail(f"the array `{name}` has an initializer or a second dimension")

    def always(self) -> None:
        at = self.pos()
        self.i += 1
        self.take("@")
        if self.peek() == "*":
            raise self.fail("an `always @*` (MyHDL writes explicit lists only)")
        end = _group_end(self.t, self.i, "(", ")") if self.peek() == "(" else None
        if end is None:
            raise self.fail("an event control that cannot be read")
        inner = self.t[self.i + 1 : end - 1]
        items: list[list[Token]] = [[]]
        depth = 0
        for tok in inner:
            if tok[1] in _CLOSE:
                depth += 1
            elif tok[1] in _CLOSE.values():
                depth -= 1
            if depth == 0 and (tok[1] == "," or (tok[0] == "id" and tok[1] == "or")):
                items.append([])
            else:
                items[-1].append(tok)
        self.i = end
        if [t[1] for t in inner] == ["*"]:
            raise self.fail("an `always @(*)` (MyHDL writes explicit lists only)")
        edges = [bool(it) and it[0][1] in ("posedge", "negedge") for it in items]
        if all(edges):
            for it in items:
                if len(it) != 2 or it[1][0] != "id" or it[1][1] in RESERVED:
                    raise self.fail("an edge item that is not `posedge name`")
            self.skip_body()
        elif any(edges):
            raise self.fail("an event list that mixes edges and level signals")
        else:
            block = _Block(self.t, self.i)
            try:
                label = block.level_block()
            except _Refuse as why:
                raise _Refuse(why.pos, why.what, block.label) from None
            self.i = block.i
            self.levels.append((items, block.reads, label, at))

    def skip_body(self) -> None:
        """Skip a `begin ... end` (nesting matched); no `@` or `wait` may be in it."""
        if self.peek() != "begin":
            raise self.fail(f"`{self.peek() or 'the end'}` where `begin` was expected")
        stack: list[str] = []
        while self.i < len(self.t):
            kind, text, _ = self.t[self.i]
            if text in ("@", "wait"):
                raise self.fail(f"`{text}` inside a block")
            if kind == "id" and text in _OPENERS:
                stack.append(_OPENERS[text])
            elif kind == "id" and text in _CLOSERS:
                want = stack.pop() if stack else ""
                if text != want and not (want == "join" and text.startswith("join")):
                    raise self.fail("begin/end, case/endcase or fork/join that do not match")
            self.i += 1
            if not stack:
                return
        raise self.fail("a block that is never closed")


def _judge_levels(m: _Module) -> tuple[int, str, str] | None:
    """The first level block whose list does not cover what it reads, or None."""
    for items, reads, label, at in m.levels:
        plain: set[str] = set()
        words: set[tuple[str, int]] = set()
        for it in items:
            shown = "".join(t[1] for t in it)
            ok_name = bool(it) and it[0][0] == "id" and it[0][1] not in RESERVED
            if ok_name and len(it) == 1:
                plain.add(it[0][1])
            elif (
                ok_name
                and len(it) == 4
                and it[1][1] == "["
                and it[3][1] == "]"
                and it[2][1].isdigit()
            ):
                if it[0][1] in m.arrays and int(it[2][1]) < m.arrays[it[0][1]]:
                    words.add((it[0][1], int(it[2][1])))  # how MyHDL lists a list of signals
                else:
                    return at, f"a level list item `{shown}` that is not a word of an array", label
            else:
                return at, f"a level list item that is not a plain signal name (`{shown}`)", label
        missing = _needs(reads, plain, words, m.arrays)
        if missing is not None:
            return (
                missing[0],
                f"a level-sensitive block that reads `{missing[1]}` without listing it",
                label,
            )
    return None


def _myhdl_level_blocks(scan: str) -> tuple[int, str, str] | None:
    """`(position, what, label)` of the first problem in a MyHDL core, or None.

    A core must be the plain single module MyHDL writes (see `_Module`); its level-sensitive
    blocks must be the plain shape `@always_comb` writes (see `_Block`) with every signal they read
    in the list. An escaped identifier anywhere is refused. Whatever the parsers do not expect is
    refused (fail closed)."""
    if "\\" in scan:  # a string is blanked in `scan`, so this is an escaped identifier
        return (
            scan.index("\\"),
            "a MyHDL core that is not the plain single module MyHDL writes (an escaped identifier)",
            "",
        )
    # the allowed directives (`timescale ...`) are lines of their own: blank them for the parser
    text = re.sub(r"`[^\n]*", lambda m: " " * len(m.group(0)), scan)
    module = _Module(_tokens(text))
    try:
        module.parse()
    except _Refuse as why:
        return why.pos, why.what, why.label
    return _judge_levels(module)


def _value(digits: str, base: str) -> int:
    """The value of the digits of a number in base `b`, `o`, `d` or `h` (-1 if they are not
    digits of that base: the tools reject such a number themselves)."""
    try:
        return int(digits.replace("_", ""), _RADIX[base.lower()])
    except ValueError:
        return -1


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
    loop_indexes = _loop_indexes(scan)
    for m in _WORD.finditer(scan):
        word = m.group(0)
        if word in TWO_STATE and m.start() in loop_indexes:
            continue  # the index of a for loop, declared in its header: set before every run
        if word in KEYWORDS or word in TWO_STATE:
            kind = "two-state type" if word in TWO_STATE else "keyword"
            return refuse(f"the {kind} `{word}`", m.start())
        if word == "enum":
            base = re.match(r"\s*(logic|reg|integer)\b", scan[m.end() :], re.ASCII)
            if base is None:
                return refuse("an `enum` without a logic, reg or integer base type", m.start())
        if word == "task":
            return refuse("a `task` (it can write any signal, and `@*` does not see it)", m.start())
        if word == "function" and not re.match(r"\s*automatic\b", scan[m.end() :], re.ASCII):
            return refuse("a `function` that is not `automatic`", m.start())
    scopes = _scope_names(scan)
    for body in _FUNCTION_BODY.finditer(scan):
        for m in _WORD.finditer(body.group(0)):
            if m.group(0) in ARGUMENT_DIRECTIONS:
                return refuse(
                    f"a function with an `{m.group(0)}` argument", body.start() + m.start()
                )
        own = _function_names(body.group(0)) - scopes
        for dotted in _DOTTED.finditer(body.group(0)):
            if dotted.group(0).split(".", 1)[0].strip() not in own:
                return refuse(
                    "a hierarchical name inside a function (a dotted name that is not a member of "
                    "an argument or local)",
                    body.start() + dotted.start(),
                )
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
        if not shown[0].isdigit() and _value(digits, m.group(1)) > UNSIZED_MAX:
            return refuse(f"the unsized number `{shown}` (2**31 or more)", m.start())
    based = [(m.start(), m.end()) for m in _BASED.finditer(scan)]
    for m in _DECIMAL.finditer(scan):
        inside = any(start <= m.start() < end for start, end in based)
        if not inside and _value(m.group(0), "d") > UNSIZED_MAX:
            return refuse(f"the unsized number `{m.group(0)}` (2**31 or more)", m.start())
    for m in re.finditer(r"#", scan):
        if not _parameter_list(scan, m.start()):
            return refuse("a delay (`#`)", m.start())
    for m in _LINT_OFF.finditer(text):  # a comment: look at the original text
        # Verilator turns off every code of a comma list (`lint_off WIDTH,BLKSEQ`, also with an
        # empty item: `WIDTH,,BLKSEQ`), so every word counts, not only the first (round 19).
        # Not the words of a `//` comment after it: Verilator reads no code there
        # (`lint_off WIDTH // no latch here` still reports LATCH; round 20)
        words = re.findall(r"[-\w]+", m.group(1).split("//")[0], re.ASCII)
        codes = [w for w in words if not w.startswith("-")]
        hidden = next((c for c in codes if c.upper() in LINT_CODES_NOT_TO_HIDE), None)
        if not codes or hidden is not None:
            code = hidden or ""
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
    ("plain single module", "Let MyHDL write the Verilog (a `@block` with `@always_comb` and "
     "`@always_seq`, converted by MyHDL): the check reads only the plain single module it writes."),
    ("macro or directive", "Use a `localparam` or `parameter` instead: a macro can build text the "
     "check never sees."),
    ("inside an escaped identifier", "Rename it to a plain name (letters, digits, `_` and `$`), "
     "or keep the back-tick and the double quote out of the escaped name."),
    ("two-state", "Use `reg` or `logic` (reset a register with rst_n): a two-state type starts "
     "at 0 in a simulator and turns an x into 0, which the check cannot see. For a loop index, "
     "declare it in the for header with no range (`for (int i = 0; i < 8; i++)`) or use "
     "`integer`."),
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
    ("a `task`", "Use a `function automatic` that takes its inputs as arguments and returns the "
     "value."),
    ("hierarchical name", "Pass the value in as an argument: Verilator and Yosys can resolve a "
     "hierarchical name to different things in a generate branch, and a simulator does not rerun "
     "an `always @*` when it changes."),
    ("function with an", "Return the value instead (several values as one concatenation): "
     "Icarus cannot compile a function argument that is not an input, and the check does not "
     "follow a value that a call writes back through one."),
    ("unsized number", "Give it a size (`32'd3000000000`): Verilator reads an unsized number of "
     "2**31 or more as a negative 32-bit value, Yosys and Icarus as a wider positive one, so "
     "the checks judged on Verilator's reading can take another branch than the proof."),
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
