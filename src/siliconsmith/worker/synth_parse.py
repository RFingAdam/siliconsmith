# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Read what Yosys wrote during synthesis: `stat -json`, the latch report, `check` reports.

Pure text in, plain data out (stdlib only, no Yosys call), so the unit tests feed it real
captured reports. Used by `synth.py` in the container.
"""

from __future__ import annotations

import json
import re
from typing import Any

from siliconsmith.worker import lint

LIBRARY_PREFIX = "sky130_fd_sc_hd__"
TOP_CELLS = 10

# sky130_fd_sc_hd cell families, by the part of the name after the library prefix.
_FLOP = re.compile(r"^(?:df|edf|sdf|sedf)[a-z]+_\d+$")  # dfxtp, dfrtp, dfbbp, edfxtp, sdfxtp ...
_LATCH = re.compile(r"^dl[xr][a-z]+_\d+$")  # dlxtp, dlxtn, dlrtp, dlrbn ... (not dlclkp, dlygate)


class StatError(ValueError):
    """The `stat -json` text cannot be used."""


def short_name(cell_type: str) -> str:
    return cell_type.removeprefix(LIBRARY_PREFIX)


def is_flip_flop(cell_type: str) -> bool:
    return bool(_FLOP.match(short_name(cell_type)))


def is_latch(cell_type: str) -> bool:
    return bool(_LATCH.match(short_name(cell_type)))


def parse_stat(text: str) -> dict[str, Any]:
    """Cells, area and per-type counts from Yosys `stat -json` (with `-liberty` for the area).

    Returns `{cells, flip_flops, latch_cells, combinational, area_um2, types}` where `types` is
    `{cell type: count}`. Yosys prints the top module under `modules` and a `design` total; the
    design total is used (the netlist is flat, so they agree).
    """
    try:
        data = json.loads(text)
        design = data["design"] if "design" in data else next(iter(data["modules"].values()))
        cells = int(design["num_cells"])
        types = {str(k): int(v) for k, v in design.get("num_cells_by_type", {}).items()}
        area = float(design.get("area", 0.0))
    except (ValueError, KeyError, TypeError, StopIteration, AttributeError) as exc:
        raise StatError(f"cannot read the Yosys statistics ({type(exc).__name__}: {exc})") from exc
    flops = sum(n for t, n in types.items() if is_flip_flop(t))
    latches = sum(n for t, n in types.items() if is_latch(t))
    return {
        "cells": cells,
        "flip_flops": flops,
        "latch_cells": latches,
        "combinational": max(cells - flops - latches, 0),
        "area_um2": area,
        "types": types,
    }


def top_cells(types: dict[str, int], limit: int = TOP_CELLS) -> list[dict[str, Any]]:
    """The most used cell types as `[{cell, count}]`, most first (ties by name)."""
    ranked = sorted(types.items(), key=lambda item: (-item[1], item[0]))
    return [{"cell": cell, "count": count} for cell, count in ranked[:limit]]


def forbidden_cells(types: dict[str, int], dont_use: set[str]) -> list[str]:
    """Cell types in the netlist that must not be there: the do-not-use lists and `lpflow_`."""
    return sorted(t for t in types if t in dont_use or "lpflow_" in t)


# -- the generic (pre-abc) area estimate ---------------------------------------------------

# Areas in um^2 of a sky130_fd_sc_hd cell that abc picks for each Yosys generic gate. They are
# calibrated (see `synth.py`, "Early stop") against full runs; they only have to be about right.
GENERIC_AREA_UM2: dict[str, float] = {
    "$_NOT_": 3.75,
    "$_AND_": 7.5,
    "$_NAND_": 5.7,
    "$_OR_": 7.5,
    "$_NOR_": 5.7,
    "$_ANDNOT_": 7.5,
    "$_ORNOT_": 7.5,
    "$_XOR_": 10.0,
    "$_XNOR_": 10.0,
    "$_MUX_": 8.5,
    "$_AOI3_": 7.5,
    "$_OAI3_": 7.5,
    "$_AOI4_": 10.0,
    "$_OAI4_": 10.0,
}
DEFAULT_GATE_AREA_UM2 = 10.0  # a gate type not listed above
# One flip-flop with its enable or reset logic, as dfflibmap and abc will build it.
FLIP_FLOP_AREA_UM2 = 30.0
LATCH_AREA_UM2 = 15.0


def estimate_generic_area(text: str) -> tuple[float, int]:
    """`(estimated um^2, generic cell count)` from the `stat -json` Yosys wrote before abc.

    Gates are priced per type (`GENERIC_AREA_UM2`); flip-flops (`$_DFF*`, `$_SDFF*`, `$_DFFE*`,
    ...) and latches (`$_DLATCH*`) are priced separately because each becomes a flop cell plus
    some logic. `$scopeinfo` and similar bookkeeping cells cost nothing.
    """
    try:
        data = json.loads(text)
        design = data["design"] if "design" in data else next(iter(data["modules"].values()))
        types = {str(k): int(v) for k, v in design.get("num_cells_by_type", {}).items()}
    except (ValueError, KeyError, TypeError, StopIteration, AttributeError) as exc:
        raise StatError(f"cannot read the Yosys statistics ({type(exc).__name__}: {exc})") from exc
    area = 0.0
    count = 0
    for cell, number in types.items():
        if cell in ("$scopeinfo", "$assert", "$print"):
            continue
        count += number
        if cell.startswith(("$_DFF", "$_SDFF", "$_ALDFF", "$_DFFSR", "$_DFFE", "$_SDFFE")):
            area += number * FLIP_FLOP_AREA_UM2
        elif cell.startswith("$_DLATCH") or cell.startswith("$_SR_"):
            area += number * LATCH_AREA_UM2
        else:
            area += number * GENERIC_AREA_UM2.get(cell, DEFAULT_GATE_AREA_UM2)
    return area, count


# -- latches -------------------------------------------------------------------------------

_LATCH_LINE = re.compile(
    r"Latch inferred for signal `(?P<signal>[^']+)' from process `(?P<proc>[^']+)'"
)
_PROC_SOURCE = re.compile(r"\$proc\$(?P<file>[\w./-]+?\.[sv]+v?):(?P<line>\d+)\$")


def plain_signal(yosys_name: str) -> str:
    """`counter.\\held` -> `held`; Yosys's own names (`$not$a.v:3$1_Y`) -> ``."""
    name = yosys_name.replace("\\", "").strip()
    if "$" in name:
        return ""
    return name.split(".", 1)[1] if "." in name else name


def project_file(path: str, name: str) -> str:
    """`counter.v` / `project.v` as Yosys saw them -> `src/counter.v` / `src/project.v`."""
    base = path.rsplit("/", 1)[-1]
    if base in (f"{name}.v", "project.v"):
        return f"src/{base}"
    return path.removeprefix("/work/")


def parse_latches(text: str, name: str) -> list[dict[str, Any]]:
    """`[{signal, file, line}]` from the `proc_dlatch` report (`reports/latch.rpt`)."""
    found: dict[tuple[str, str, int], dict[str, Any]] = {}
    for match in _LATCH_LINE.finditer(text):
        source = _PROC_SOURCE.search(match["proc"])
        file = project_file(source["file"], name) if source else ""
        line = int(source["line"]) if source else 0
        signal = plain_signal(match["signal"]) or match["signal"].replace("\\", "")
        found.setdefault((signal, file, line), {"signal": signal, "file": file, "line": line})
    return list(found.values())


# -- check reports -------------------------------------------------------------------------

KINDS = {"MULTIDRIVEN": "multiple_drivers", "LOGICLOOP": "comb_loop", "NODRIVER": "undriven"}


def _declaration_line(core_text: str, signal: str) -> int:
    """The first line of the core's text that mentions `signal` as a whole word, or 0."""
    word = re.compile(rf"(?<![\w$]){re.escape(signal)}(?![\w$])")
    for number, line in enumerate(core_text.splitlines(), start=1):
        if word.search(line):
            return number
    return 0


def parse_checks(texts: list[str], name: str, core_text: str = "") -> list[dict[str, Any]]:
    """Problems from Yosys `check` reports: `[{kind, signal, file, line, message}]`.

    `kind` is `comb_loop`, `multiple_drivers` or `undriven`. `file` and `line` come from the
    source attribute Yosys put in the report (a cell's `source: counter.v:11.14-11.16` or its
    name); a signal with no source is looked up by name in `core_text` (the line where the name
    first appears, usually its declaration). The same problem seen by several reports is listed
    once.
    """
    out: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for text in texts:
        for item in lint.parse_yosys_check(text, f"src/{name}.v"):
            kind = KINDS.get(str(item["code"]))
            if kind is None:
                continue  # "ERROR: Found N problems" with no warning to explain it
            signal = plain_signal(str(item.get("signal", "")))
            file = (
                project_file(item["source_file"], name)
                if item.get("source_file")
                else (f"src/{name}.v")
            )
            line = int(item["line"])
            if not line and signal and core_text:
                line = _declaration_line(core_text, signal)
            message = str(item["msg"])
            if signal and kind != "comb_loop":
                message = {
                    "multiple_drivers": f"{signal} is driven from more than one place",
                    "undriven": f"{signal} is used but nothing drives it",
                }[kind]
            elif kind == "multiple_drivers":
                message = "a net is driven from more than one place"
            out.setdefault(
                (kind, signal, file, line),
                {"kind": kind, "signal": signal, "file": file, "line": line, "message": message},
            )
    return list(out.values())
