# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Read what Yosys printed and wrote during the equivalence check (pure functions, no Yosys).

The text formats are Yosys 0.62's: `equiv_status`, the `sat -show` tables, `write_json`. They are
pinned by unit tests on saved logs (`tests/fixtures/equiv/`). Stdlib only: this runs in the
container.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from siliconsmith.worker import equiv_script as es

# Every Verilog spelling of a constant with an x, z or ? digit: optional size, optional blanks
# around the sign and base letter (`1 'b z`, `'hz`, `1'sbz`, `8'o7z`), and the unsized fill
# constants `'x` and `'z`. A guard in front of Yosys; the netlist script also turns any x/z that
# gets through into a free value, so this is not the only protection.
XZ_LITERAL = re.compile(
    r"(?:\d[\d_]*\s*)?'\s*(?:[sS]\s*)?[bBoOdDhH]\s*[0-9a-fA-F_]*[xXzZ?]|'\s*[xXzZ]"
)

# What each guard refusal means: (verdict, reason code, plain English).
GUARD_VERDICTS: dict[str, tuple[str, str, str]] = {
    es.M_CLOCKING: (
        "not_proven",
        "guard_clocking",
        "the design has a latch, a memory the proof cannot model, or a flip-flop that is not "
        "clocked on the rising edge of the top-level clk (a falling-edge flip-flop or a clock "
        "made inside the design); these are not supported",
    ),
    es.M_TRISTATE: (
        "not_proven",
        "guard_tristate",
        "the design has tri-state logic ('z' in the RTL or an ebufn cell in the netlist), which "
        "is not supported; on Tiny Tapeout drive uio_out and set uio_oe instead of writing z",
    ),
    es.M_ASYNC_SR: (
        "not_proven",
        "guard_async_set_reset",
        "the netlist has a flip-flop with both an asynchronous set and an asynchronous reset "
        "(sky130 dfbb*): the library's own models disagree when both are active, so it is not "
        "supported",
    ),
    es.M_STRUCTURE: (
        "error",
        "structure",
        "the design or the netlist has a combinational loop, a net with several drivers, or a "
        "net nothing drives (a cell input left open)",
    ),
    es.M_CELLS: (
        "error",
        "cells",
        "the netlist has a cell that is not a plain logic gate (a cell without a logic function, "
        "or behavioural code such as a bit select or a division)",
    ),
    es.M_MODULES: (
        "error",
        "netlist_modules",
        "the netlist file defines a module of its own besides the design (a sky130 cell written "
        "out by hand), or hides one behind an attribute: the check only trusts the library's "
        "cells",
    ),
}

_ERROR_MARKS = ("ERROR", "logic loop", "conflicting drivers")


# An x, z, m or - bit in an RTLIL constant (`1'z`, `8'0000000z`): what a netlist spelled any way
# at all comes out as once Yosys has read it.
RTLIL_XZ = re.compile(r"(?<![\w$\\])\d+'[01]*[xzm\-][01xzm\-]*")


def rtlil_xz_constant(rtlil_text: str) -> str | None:
    """The first x/z constant in the netlist as Yosys read it (RTLIL text), or None."""
    found = RTLIL_XZ.search(rtlil_text)
    return found.group(0) if found else None


def xz_constant(netlist_text: str) -> str | None:
    """The first x/z/? constant in the netlist text, or None."""
    found = XZ_LITERAL.search(netlist_text)
    return found.group(0) if found else None


INCLUDE = re.compile(r"`\s*include\b")
_SRC_ATTR = re.compile(r"\(\*[^)]*\bsrc\b", re.S)
_MODULE = re.compile(r"(?<![\w$])(?:macro)?module\s+(\\?[\w$.]+)")


def include_directive(text: str) -> bool:
    """True if the text has an `include: the check only reads the files it is given."""
    return INCLUDE.search(text) is not None


def netlist_text_problem(text: str, top: str) -> str | None:
    """What the netlist text has that only a hand-made file has: an `include, a module besides
    the top, or an `(* src ... *)` attribute (which could hide a module from a source check)."""
    if include_directive(text):
        return "an `include"
    if _SRC_ATTR.search(text):
        return "a `src` attribute"
    for found in _MODULE.finditer(text):
        if found.group(1).lstrip("\\") != top:
            return f"a module of its own ({found.group(1)})"
    return None


def guard_failure(rc: int | None, log: str) -> dict[str, Any] | None:
    """Map a guard refusal or a tool failure to `{verdict, reason, message, detail}`.

    None means the script ran to the end (rc 0). rc None means the call was killed at its time
    limit.
    """
    if rc == 0:
        return None
    if rc is None:
        return {
            "verdict": "not_proven",
            "reason": "time_limit",
            "message": "a Yosys step ran out of time",
            "detail": [],
        }
    lines = log.splitlines()
    marks = re.findall(r"^(GUARD-\w+)\s*$", log, re.M)
    errors: list[str] = []
    for i, line in enumerate(lines):  # error lines, plus the objects a failed select names
        if any(m in line for m in _ERROR_MARKS) and line.strip() not in errors:
            errors.append(line.strip())
        if line.startswith("Selection contains:"):
            errors += [x.strip() for x in lines[i + 1 : i + 6] if "/" in x]
    detail = list(dict.fromkeys(errors))[-6:]
    last = marks[-1] if marks else ""
    unknown = re.search(
        r"Module `\\?(\S+?)' referenced in module .* is not part of the design", log
    )
    if unknown is not None:  # a cell the liberty file gives no function for (a clock gate, say)
        return {
            "verdict": "error",
            "reason": "cells",
            "message": f"the netlist uses a cell the check cannot model ({unknown.group(1)}): it "
            "has no logic function in the library",
            "detail": detail,
        }
    if last in GUARD_VERDICTS:
        verdict, reason, message = GUARD_VERDICTS[last]
        return {"verdict": verdict, "reason": reason, "message": message, "detail": detail}
    return {
        "verdict": "error",
        "reason": "yosys_failed",
        "message": "Yosys stopped with an error",
        "detail": detail,
    }


def has_inits(log: str) -> bool:
    """True if the RTL carried initial values (`a:init` objects listed between the markers)."""
    block = re.search(rf"^{es.INITS_BEGIN}\n(.*?)^{es.INITS_END}", log, re.M | re.S)
    return bool(block and re.search(r"\S/\S", block.group(1)))


def has_memories(log: str) -> bool:
    """True if the RTL has a memory (`$mem*` cells listed between the markers)."""
    block = re.search(rf"^{es.MEMS_BEGIN}\n(.*?)^{es.MEMS_END}", log, re.M | re.S)
    return bool(block and re.search(r"\S/\S", block.group(1)))


def parse_stage1(log: str) -> dict[str, Any] | None:
    """`equiv_status`: counts, unproven points and the output signals that were paired."""
    m = re.search(r"Of those cells (\d+) are proven and (\d+) are unproven", log)
    if m is None:
        return None
    cells = re.findall(r"Unproven \$equiv \S+: \\(\S+?)_gold( \[\d+\])? ", log)
    paired = set(re.findall(r"Presumably equivalent wires: (\S+)_gold", log))
    return {
        "proven": int(m.group(1)),
        "unproven": int(m.group(2)),
        "unproven_signals": sorted({b + i.strip() for b, i in cells}),
        "unpaired_outputs": [p for p in es.OUTPUTS if p not in paired],
    }


def table(log: str) -> dict[int, dict[str, str]]:
    """`sat -show` table -> {step: {signal: bits}} (rows: `step name dec hex bin`, `--` for x)."""
    vals: dict[int, dict[str, str]] = {}
    for line in log.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].isdigit() and re.fullmatch(r"[01xz]+", f[-1]):
            vals.setdefault(int(f[0]), {})[" ".join(f[1:-3]).lstrip("\\")] = f[-1]
    return vals


def show_bits(bits: str | None) -> str:
    """One bit as it is, several defined bits as hex, else the bit string with x (`0b0x01`)."""
    if bits is None:
        return "x"
    if len(bits) == 1:
        return bits
    if re.fullmatch(r"[01]+", bits):
        return f"0x{int(bits, 2):0{(len(bits) + 3) // 4}x}"
    return "0b" + bits


# -- the stage-1 model -----------------------------------------------------------------------


def rtlil_sig(name: str, width: int, i: int) -> str:
    """RTLIL text for bit `i` (raw position, LSB = 0) of a wire: the parser ignores offsets."""
    n = name if name.startswith("$") else "\\" + name
    return n if width == 1 else f"{n} [{i}]"


def equiv_points(equiv_json: str) -> tuple[list[es.Point], int, int]:
    """The `$equiv` cells of the stage-1 model, and the flip-flop bits of each side.

    A point is tied (the netlist flop may power up equal to the RTL register) when its RTL bit is
    the Q of a `$dff` (the RTL side) and its netlist bit the Q of a `$_DFF_P_`, and the RTL bit
    is not already tied to another flop: each RTL bit is tied to one flop only, so ties never
    restrict the netlist's power-up state.
    """
    mod = json.loads(equiv_json)["modules"]["equiv"]
    names: dict[int, tuple[tuple[int, int], str]] = {}  # bit -> (rank, RTLIL signal)
    label: dict[int, str] = {}
    for n, w in mod["netnames"].items():
        bits, off, upto = w["bits"], w.get("offset", 0), w.get("upto", 0)
        hide = int(w.get("hide_name", 0))
        for i, b in enumerate(bits):
            if not isinstance(b, int):
                continue
            idx = off + (len(bits) - 1 - i if upto else i)
            rank = (hide, len(n))
            if b not in names or rank < names[b][0]:
                names[b] = (rank, rtlil_sig(n, len(bits), i))
                if not hide:
                    label[b] = n if len(bits) == 1 else f"{n}[{idx}]"

    def sig(b: int | str) -> str:
        return f"1'{b}" if isinstance(b, str) else names[b][1]

    ffq: dict[str, set[int]] = {"_gold": set(), "_gate": set()}
    for cn, c in mod["cells"].items():
        if c["type"] in ("$dff", "$_DFF_P_"):
            for side in ffq:
                if cn.endswith(side):
                    ffq[side].update(b for b in c["connections"]["Q"] if isinstance(b, int))
    points: list[es.Point] = []
    chosen: dict[int, int] = {}
    for c in mod["cells"].values():
        if c["type"] == "$equiv":
            a, b, y = (c["connections"][p][0] for p in "ABY")
            tie = (
                isinstance(a, int)
                and isinstance(b, int)
                and a in ffq["_gold"]
                and b in ffq["_gate"]
                and chosen.setdefault(a, b) == b
            )
            points.append(
                es.Point(
                    label.get(y, sig(y)) if isinstance(y, int) else sig(y), sig(a), sig(b), tie
                )
            )
    return points, len(ffq["_gold"]), len(ffq["_gate"])


# The flip-flops the guards let through (`GUARD_CLOCK`): the RTL side's `$dff` (rising edge) and
# the netlist side's `$_DFF_P_`. Any other cell with state is refused by the power-up models.
FLIP_FLOPS = frozenset({"$dff", "$_DFF_P_"})
_STATEFUL = re.compile(
    r"dff|latch|mem|anyinit|anyconst|anyseq|allconst|allseq|initstate|fsm|^\$_?ff_?$|^\$_?sr(_|$)",
    re.I,
)
_CELL = re.compile(r"^  cell (\S+) (\S+)$")
_WIRE = re.compile(r"^  wire (?:.* )?(\S+)$", re.M)
SIDES = ("gold", "gate")


def side_by_suffix(cell: str) -> str | None:
    """The side of a cell of the stage-1 model: `equiv_make` names them `<name>_gold`/`_gate`."""
    return next((k for k in SIDES if cell.endswith(f"_{k}")), None)


def side_by_prefix(cell: str) -> str | None:
    r"""The side of a cell of a flattened miter: `flatten` names them after the instance first
    (`\gold.core.r`, `$flatten\gate.\_093_.$auto$...`)."""
    head, dot, _ = cell.removeprefix("$flatten").removeprefix("\\").partition(".")
    return head if dot and head in SIDES else None


def flip_flops(il: str, side_of: Callable[[str], str | None]) -> dict[str, list[tuple[str, int]]]:
    """The Q of every flip-flop of a one-module RTLIL model, as signal text with its width, for
    each side (`side_of` reads the side from the cell name). Raises ValueError when the power-up
    value of some state cannot be set: a cell with state that is not one of `FLIP_FLOPS` (a
    latch, a memory, another flip-flop type), a flip-flop of neither side or without a Q or
    width, an `init` attribute (it would fix a power-up value) or more than one module."""
    if len(re.findall(r"^module ", il, re.M)) != 1:
        raise ValueError("the proof model is not one module")
    if re.search(r"^\s*attribute \\init ", il, re.M):
        raise ValueError("the proof model has an initial value")
    found: dict[str, list[tuple[str, int]]] = {k: [] for k in SIDES}
    cell: tuple[str, str] | None = None
    q: str | None = None
    width: int | None = None
    for line in il.splitlines():
        start = _CELL.match(line)
        if start is not None:
            cell, q = (start.group(1), start.group(2)), None
            width = 1 if cell[0] == "$_DFF_P_" else None
            if cell[0] not in FLIP_FLOPS and _STATEFUL.search(cell[0]):
                raise ValueError(f"the proof model has a cell with state it cannot set: {cell[0]}")
        elif cell is None:
            continue
        elif line.startswith("    connect \\Q "):
            q = line[len("    connect \\Q ") :].strip()
        elif cell[0] == "$dff" and line.startswith("    parameter \\WIDTH "):
            width = int(line.split()[-1])
        elif line == "  end":
            if cell[0] in FLIP_FLOPS:
                side = side_of(cell[1])
                if side is None or not q or not width:
                    raise ValueError(f"a flip-flop of the proof model cannot be read: {cell[1]}")
                found[side].append((q, width))
            cell = None
    return found


def _power_up_wires(
    il: str, add: list[str], side_of: Callable[[str], str | None]
) -> tuple[str, dict[str, str]]:
    """`il` plus the lines `add` and, for each side with flip-flops, one wire that holds the Q
    of every flip-flop of that side. Returns the model and `{side: wire name}` (a name the model
    does not use yet). Raises ValueError like `flip_flops`."""
    used = set(_WIRE.findall(il))
    wires: dict[str, str] = {}
    lines = list(add)
    for side, flops in flip_flops(il, side_of).items():
        if not flops:
            continue
        name, n = f"ff_{side}", 0
        while f"\\{name}" in used:
            n += 1
            name = f"ff_{side}_{n}"
        wires[side] = name
        lines += [
            f"  wire width {sum(w for _, w in flops)} \\{name}",
            f"  connect \\{name} {{ {' '.join(q for q, _ in flops)} }}",
        ]
    end = il.rstrip().rfind("\nend")
    return il[:end] + "\n" + "\n".join(lines) + il[end:], wires


def base_model(equiv_il: str, points: list[es.Point]) -> tuple[str, dict[str, str]]:
    """`equiv.il` plus a named wire for each side of every point (`eqa_i`, `eqb_i`) and the
    power-up wires of `_power_up_wires` (stage 3, `es.base_script`)."""
    add: list[str] = []
    for i, p in enumerate(points):
        add += [
            f"  wire \\eqa_{i}",
            f"  wire \\eqb_{i}",
            f"  connect \\eqa_{i} {p.a}",
            f"  connect \\eqb_{i} {p.b}",
        ]
    return _power_up_wires(equiv_il, add, side_by_suffix)


def miter_model(miter_il: str) -> tuple[str, dict[str, str]]:
    """The flattened miter (`miter.il`) plus the power-up wires of `_power_up_wires` (the
    referee of stage 4, `es.miter_bmc`)."""
    return _power_up_wires(miter_il, [], side_by_prefix)


# -- stage 3: the base case ------------------------------------------------------------------


BASE_RUNS = ("X", "DEF")
SAT_PROVED = "no model found: SUCCESS"
SAT_FAILED = "model found: FAIL"


def parse_base(log: str, points: list[es.Point]) -> dict[str, Any]:
    """Read the two base-case runs. `{"status": "ok"}` only when BOTH runs print a clean proof
    (`no model found: SUCCESS`, once each); `{"status": "cex", ...}` for the first difference
    (run, cycle, the differing points, the inputs); `{"status": "unreadable", "run"}` for a run
    that is missing, cut off or says neither: a log that cannot be read is never a pass."""
    parts = {
        part.split()[0]: part
        for part in re.split(rf"^{es.BASE_PREFIX}", log, flags=re.M)[1:]
        if part.split()
    }
    for run in BASE_RUNS:  # the X run first: it is the stricter one
        part = parts.get(run)
        if part is None:
            return {"status": "unreadable", "run": run}
        if SAT_FAILED in part:
            vals = table(part)
            for t in sorted(vals):
                if run == "DEF" and t == 1:
                    continue  # the DEF run starts from defined values: cycle 1 proves nothing
                row = vals[t]
                diffs = [
                    {"signal": p.label, "rtl": row.get(f"eqa_{i}"), "netlist": row.get(f"eqb_{i}")}
                    for i, p in enumerate(points)
                    if row.get(f"eqa_{i}") in ("0", "1")
                    and row.get(f"eqb_{i}") != row.get(f"eqa_{i}")
                ]
                if diffs:
                    return {
                        "status": "cex",
                        "run": run,
                        "cycle": t,
                        "differs": diffs,
                        "inputs": {k: row.get(k) for k in ("rst_n", "ui_in", "uio_in")},
                        "outputs_differ": any(
                            str(d["signal"]).startswith(es.OUTPUTS) for d in diffs
                        ),
                    }
            return {"status": "unreadable", "run": run}
        if part.count(SAT_PROVED) != 1:
            return {"status": "unreadable", "run": run}
    return {"status": "ok"}


# -- stages 2 and 4: counterexamples ----------------------------------------------------------


def parse_cex(log: str) -> dict[str, Any]:
    """A `sat -show-ports` counterexample: the per-cycle trace and the first mismatch (from
    cycle 2) as `{"cycle", "differs": [{"signal", "rtl", "netlist"}]}`."""
    vals = table(log)
    trace: list[dict[str, Any]] = []
    first: dict[str, Any] | None = None
    for t in sorted(vals):
        row = vals[t]
        diffs: list[dict[str, str]] = []
        for k, gv in row.items():
            hv = row.get("gate_" + k[5:]) if k.startswith("gold_") else None
            if hv is None or len(hv) != len(gv):
                continue
            diffs += [
                {"signal": f"{k[5:]}[{len(gv) - 1 - i}]", "rtl": a, "netlist": b}
                for i, (a, b) in enumerate(zip(gv, hv, strict=True))
                if a != b and a in "01"
            ]
        trace.append(
            {
                "cycle": t,
                "inputs": {
                    k[3:]: v for k, v in row.items() if k in ("in_rst_n", "in_ui_in", "in_uio_in")
                },
                "rtl": {k[5:]: v for k, v in row.items() if k.startswith("gold_")},
                "netlist": {k[5:]: v for k, v in row.items() if k.startswith("gate_")},
            }
        )
        if diffs and first is None and t >= 2:
            first = {"cycle": t, "differs": diffs}
    return {"first_mismatch": first, "trace": trace}


def covered_cycles(log: str) -> int:
    """How many cycles from reset a bounded or inductive search covered without a difference."""
    return max(
        (
            int(n)
            for n in re.findall(
                r"(?:Base case for induction length|proved base case for) (\d+)", log
            )
        ),
        default=0,
    )


def induction_length(log: str) -> int | None:
    k = re.findall(r"Trying induction with length (\d+)", log)
    return int(k[-1]) if k else None


def state_result(log: str) -> str:
    """The state check (stage 2): `equal`, `differ`, `unusable` (some register could not be
    exposed, so the check cannot see it) or `unreadable` (never read as equal)."""
    marked = log.rfind(es.M_EXPOSE)
    if marked >= 0 and "ERROR" in log[marked:]:
        return "unusable"
    if SAT_FAILED in log:
        return "differ"
    if log.count(SAT_PROVED) == 1:
        return "equal"
    return "unreadable"


def reached_depth(log: str, depth: int) -> bool:
    """True if a bounded search printed its positive end line for `depth` steps."""
    return f"proved base case for {depth} steps: SUCCESS" in log
