# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The `waves` verb: read a VCD or FST file with pywellen and return a compact change table.

The file is one the `test` verb kept under `build/waves/`. The format is found from the first
bytes, not from the file name. Signals are picked by full hierarchical name or by a suffix of it;
if a suffix matches several signals, the one nearest the top wins when that is unique
(`uo_out` is `tb.uo_out`, not `tb.user_project.uo_out`). Verilator writes some scopes twice
(`TOP`, `$rootio`); those copies are merged. The table has one row per time where at least one
requested signal changed, with the values in effect then. `pywellen` is imported inside the
function that needs it; everything else is stdlib.
"""

from __future__ import annotations

import difflib
import math
import os
import re
from pathlib import Path
from typing import Any

from siliconsmith.worker.limits import (
    MAX_SUGGESTIONS,
    MAX_WAVE_ROWS,
    MAX_WAVE_SIGNALS,
    WAVE_SIGNAL_MAX,
    WAVE_VALUE_MAX,
    clip,
)

WAVES_DIR = "/work/build/waves"
PATH_RE = re.compile(r"^build/waves/(icarus|verilator)/tb\.(fst|vcd)$")
SIGNAL_RE = re.compile(r"^[A-Za-z_$\\][\w$.\[\]:\\ -]{0,299}$")
_UNIT_NS = {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "µs": 1e3, "ms": 1e6, "s": 1e9}
_DUPLICATE_ROOTS = ("TOP.", "$rootio.")
MAX_CHANGES_PER_SIGNAL = 2_000_000


def check_request(
    request: dict[str, Any],
) -> tuple[str, list[str], float | None, float | None, int]:
    path, signals = request.get("path"), request.get("signals")
    if not isinstance(path, str) or not PATH_RE.fullmatch(path):
        raise ValueError(f"bad wave path {path!r}")
    if (
        not isinstance(signals, list)
        or not 1 <= len(signals) <= MAX_WAVE_SIGNALS
        or not all(isinstance(s, str) and SIGNAL_RE.fullmatch(s) for s in signals)
    ):
        raise ValueError("signals must be 1 to 16 signal names")
    bounds: list[float | None] = []
    for key in ("start_ns", "end_ns"):
        value = request.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise ValueError(f"{key} must be a number")
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{key} must be finite")
        bounds.append(None if value is None else float(value))
    max_rows = request.get("max_rows")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or not max_rows >= 1:
        raise ValueError("max_rows must be a positive integer")
    return path, [str(s) for s in signals], bounds[0], bounds[1], min(max_rows, MAX_WAVE_ROWS)


def sniff_format(path: Path) -> str | None:
    """`vcd` or `fst` by the first bytes (the extension is not trusted), else None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(4096)
    except OSError:
        return None
    text = head.lstrip()
    if text.startswith((b"$date", b"$version", b"$timescale", b"$scope", b"$comment", b"$var")):
        return "vcd"
    # An FST file starts with a hierarchy-less block header: block type 0 (HEADER) first.
    if len(head) >= 9 and head[0] == 0 and head[1:9] == (329).to_bytes(8, "big"):
        return "fst"
    return None


def _normalize(full: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in _DUPLICATE_ROOTS:
            if full.startswith(prefix):
                full, changed = full[len(prefix) :], True
    return full


def resolve(requested: str, names: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """`(full name, None)` or `(None, {signal, kind, matches})` for one requested name."""
    if requested in names:
        return requested, None
    wanted = _normalize(requested)
    if wanted in names:
        return wanted, None
    hits = [full for full in names if full == wanted or full.endswith("." + wanted)]
    if len(hits) > 1:
        top = min(full.count(".") for full in hits)
        nearest = [full for full in hits if full.count(".") == top]
        if len(nearest) == 1:
            return nearest[0], None
        return None, {
            "signal": requested,
            "kind": "ambiguous",
            "matches": sorted(hits)[:MAX_SUGGESTIONS],
        }
    if hits:
        return hits[0], None
    short = {full.rsplit(".", 1)[-1] for full in names}
    close = difflib.get_close_matches(wanted, sorted(names), n=MAX_SUGGESTIONS, cutoff=0.5)
    for near in difflib.get_close_matches(
        wanted.rsplit(".", 1)[-1], sorted(short), n=4, cutoff=0.6
    ):
        close += [f for f in sorted(names) if f.endswith("." + near) and f not in close][:2]
    return None, {"signal": requested, "kind": "unknown", "matches": close[:MAX_SUGGESTIONS]}


def format_value(value: Any, width: int | None, is_bit: bool) -> str:
    """`0`/`1`/`x`/`z` for a bit; `0x..` hex for a bus (an `x`/`z` nibble stays `x`/`z`)."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, float):
        return clip(repr(value), WAVE_VALUE_MAX)
    if isinstance(value, int):
        if is_bit or width == 1:
            return str(value & 1)
        digits = max(1, -(-(width or value.bit_length() or 1) // 4))
        return clip(f"0x{value:0{digits}x}", WAVE_VALUE_MAX)
    text = str(value).lower()
    if is_bit or width == 1 or not set(text) <= set("01xzuwlh-"):
        return clip(text, WAVE_VALUE_MAX)
    text = text.rjust(-(-len(text) // 4) * 4, "0")
    nibbles = []
    for i in range(0, len(text), 4):
        chunk = text[i : i + 4]
        if set(chunk) <= {"0", "1"}:
            nibbles.append(f"{int(chunk, 2):x}")
        else:
            nibbles.append("z" if set(chunk) == {"z"} else "x")
    return clip("0x" + "".join(nibbles), WAVE_VALUE_MAX)


def _time_text(ns: float) -> str:
    text = f"{ns:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _open(path: str) -> Any:
    """Open the wave file with pywellen (inside the image)."""
    from pywellen import Waveform  # in the image only

    return Waveform(path)


def window_changes(
    signal: Any, tick_ns: float, start_ns: float | None, end_ns: float | None
) -> tuple[Any, list[tuple[float, Any]]]:
    """`(value in effect before the window, changes inside it)` for one signal.

    `signal` is a pywellen Signal: `len()` and integer indexing give `(time, value)` pairs in time
    order, so the start of the window is found by bisection and nothing outside the window is
    read. At most MAX_CHANGES_PER_SIGNAL changes are returned. Times are in nanoseconds.
    """
    count = len(signal)
    low, high = 0, count
    if start_ns is not None:
        while low < high:  # first change at or after start_ns
            mid = (low + high) // 2
            if signal[mid][0] * tick_ns < start_ns:
                low = mid + 1
            else:
                high = mid
    before = signal[low - 1][1] if low > 0 and start_ns is not None else None
    changes: list[tuple[float, Any]] = []
    for index in range(low, min(count, low + MAX_CHANGES_PER_SIGNAL)):
        time, value = signal[index]
        time_ns = time * tick_ns
        if end_ns is not None and time_ns > end_ns:
            break
        changes.append((time_ns, value))
    return before, changes


def _checked_file(rel: str) -> Path | None:
    """The wave file for `rel`, or None if it is missing; refuses symlinks on the way."""
    parts = rel.split("/")
    current = Path("/work")
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"{rel} goes through a symbolic link")
    return current if current.is_file() else None


def read(request: dict[str, Any]) -> dict[str, Any]:
    path, requested, start_ns, end_ns, max_rows = check_request(request)
    base: dict[str, Any] = {
        "state": "ok",
        "format": None,
        "timescale": None,
        "columns": [],
        "rows": [],
        "rows_total": 0,
        "t_first_ns": None,
        "t_last_ns": None,
        "problems": [],
    }
    file = _checked_file(path)
    if file is None:
        return {**base, "state": "missing"}
    fmt = sniff_format(file)
    if fmt is None:
        return {**base, "state": "bad_format"}
    wave = _open(str(file))
    base["format"] = fmt
    scale = _UNIT_NS.get(str(wave.timescale.unit))
    if scale is None:
        return {**base, "state": "bad_format"}
    tick_ns = float(wave.timescale.factor) * scale
    base["timescale"] = f"{wave.timescale.factor} {wave.timescale.unit}"
    names: dict[str, Any] = {}
    for var in wave.all_vars():
        names.setdefault(
            _normalize(str(var.full_name)), var
        )  # a second copy of a scope (Verilator) is dropped
    chosen: list[tuple[str, Any]] = []
    problems = []
    for want in requested:
        full, problem = resolve(want, names)
        if problem is not None:
            problem["matches"] = [clip(m, WAVE_SIGNAL_MAX) for m in problem["matches"]]
            problems.append(problem)
        elif full is not None and all(full != c[0] for c in chosen):
            chosen.append((full, names[full]))
    if problems:
        return {**base, "state": "signal_problem", "problems": problems}
    columns = []
    series: list[list[tuple[float, str]]] = []
    current: list[str] = []
    for full, var in chosen:
        width = var.bitwidth
        is_bit = bool(var.is_1bit)
        before, changes = window_changes(var.tv, tick_ns, start_ns, end_ns)
        series.append([(t, format_value(v, width, is_bit)) for t, v in changes])
        current.append("-" if before is None else format_value(before, width, is_bit))
        columns.append(
            {
                "name": clip(full, WAVE_SIGNAL_MAX),
                "width": width if isinstance(width, int) else None,
            }
        )
    times = sorted({t for column in series for t, _ in column})
    rows: list[list[Any]] = []
    rows_total = 0
    pointers = [0] * len(series)
    last: list[str] | None = None
    # The state in effect when the window starts, unless a change lands exactly there.
    if (
        start_ns is not None
        and any(value != "-" for value in current)
        and (not times or times[0] > start_ns)
    ):
        rows.append([_time_text(start_ns), *current])
        rows_total += 1
        last = list(current)
    for time in times:
        for index, column in enumerate(series):
            while pointers[index] < len(column) and column[pointers[index]][0] <= time:
                current[index] = column[pointers[index]][1]
                pointers[index] += 1
        if last is not None and last == current:
            continue  # nothing changed in the requested columns
        last = list(current)
        rows_total += 1
        if len(rows) < max_rows:  # later rows are only counted, never built
            rows.append([_time_text(time), *current])
    base["columns"] = columns
    base["rows_total"] = rows_total
    base["rows"] = rows
    if times:
        base["t_first_ns"] = _time_text(times[0])
        base["t_last_ns"] = _time_text(times[-1])
    return base


def run(request: dict[str, Any]) -> dict[str, Any]:
    """The `waves` verb."""
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return read(request)
