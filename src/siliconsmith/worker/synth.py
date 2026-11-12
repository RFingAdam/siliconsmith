# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The `synth` verb: emit (re-run in full), then Yosys turns the Verilog into sky130 cells.

One container call. The emit pipeline in `worker/emit.py` runs first and its result is returned
as it is (same elaboration and lint errors as `emit_verilog`); only when it is ok does Yosys
start. The Yosys script comes from `synth_script.py` (LibreLane 3.0.14's synthesis order) and
runs in two halves in `<run_dir>/out/synth/`:

1. Front half: read the Verilog, generic synthesis and optimization into plain gates and
   flip-flops (`front.log`, `reports/`, `generic.il`).
2. Early stop: the gates are priced with a calibrated per-gate area (`synth_parse`), and a
   design whose estimate is far over the tile's cell budget stops here, before `abc`.
3. Back half: map to sky130 cells (`dfflibmap`, `abc`, ...) and write the netlist
   `<run_dir>/out/synth/tt_um_<name>.v` and `reports/stat.json`.

Then the reports are read: statistics, latches (with the source line of the `always` block),
and the `check` problems (loops, multiple drivers, undriven nets) with source locations. The
host copies the netlist and statistics out of the run folder into `build/synth/`.

## Early stop: calibration

The per-gate areas in `synth_parse.GENERIC_AREA_UM2` and the flip-flop price (30 um2, with its
enable or reset logic) are the sky130 areas of the cells `abc` typically picks. The estimate is
made before `abc`, so it is a rough guide, and the stop factor (`utilization.PRE_ABC_STOP_FACTOR`
= 3, applied by the host to the cell budget) is generous.

Measured in the pinned image (Yosys 0.62 YoWASP, 2 CPUs, 2026-11-12), estimate divided by the
final area after `abc`: counter 0.88, 256-bit shift register 0.96, 256-entry ROM 1.08, registered
16x16 / 24x24 / 32x32 / 48x48 / 64x64 multipliers 0.98 / 0.96 / 0.96 / 0.97 / 0.97. A design that
fits a tile (utilization up to 1.0) is therefore never stopped, whatever its kind.

Timings: the front half takes 1.0 s for the counter and 10.7 s for the 64x64 oversize fixture
(22,441 cells after `abc`, 225,000 um2); the `abc` half takes 3.8 s and 100 s. The oversize
fixture is stopped after the front half: 9.9 s of Yosys time, 11.7 s for the whole tool call.

Stdlib only at module level.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from siliconsmith.worker import emit, myhdl_map, synth_parse, synth_script
from siliconsmith.worker.limits import (
    CELL_NAME_MAX,
    FILE_MAX,
    FINDING_MSG_MAX,
    MAX_FORBIDDEN_CELLS,
    MAX_SYNTH_FINDINGS,
    MAX_SYNTH_LOG_LINES,
    MESSAGE_MAX,
    SYNTH_SIGNAL_MAX,
    clip,
)

SYNTH_DIR = "synth"
# Time kept back for reading the reports and writing the result when the host's limit is near.
RESERVE_S = 20.0
_ERROR_LINE = re.compile(r"ERROR|error:", re.IGNORECASE)


def _number(request: dict[str, Any], key: str, *, positive: bool = True) -> float:
    value = request.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number")
    if positive and not value > 0:
        raise ValueError(f"{key} must be above 0")
    return float(value)


def _read(path: Path, limit: int = 8 << 20) -> str:
    try:
        with open(path, "rb") as handle:
            return handle.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _outcome(state: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": state,
        "yosys_seconds": 0.0,
        "message": None,
        "log": [],
        "estimate": None,
        "stat": None,
        "top_cells": [],
        "forbidden_cells": [],
        "latches": [],
        "checks": [],
        "netlist": None,
        "netlist_sha256": None,
    }
    base.update(fields)
    return base


class _Timeout(Exception):
    def __init__(self, half: str) -> None:
        super().__init__(half)
        self.half = half


def _yosys(
    script: Path, log: Path, cwd: Path, half: str, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    """Run Yosys quietly on one script; the full log goes to `log`."""
    if timeout_s <= 0:
        raise _Timeout(half)
    try:
        return subprocess.run(
            ["yosys", "-q", "-l", str(log), "-s", str(script)],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        emit._reap_processes()
        raise _Timeout(half) from None


def _error_lines(
    proc: subprocess.CompletedProcess[str], log: Path, name: str, out_dir: Path
) -> list[str]:
    """The lines that say why Yosys stopped: `ERROR` lines first, else the end of the output."""
    text = proc.stderr + "\n" + proc.stdout
    lines = [ln.rstrip() for ln in emit._out_paths(text, name, out_dir).splitlines() if ln.strip()]
    errors = [ln for ln in lines if _ERROR_LINE.search(ln)]
    if not errors:
        logged = emit._out_paths(_read(log), name, out_dir).splitlines()
        errors = [ln for ln in logged if _ERROR_LINE.search(ln)] or lines[-MAX_SYNTH_LOG_LINES:]
    return [
        clip(emit._project_files(ln, name), FINDING_MSG_MAX) for ln in errors[:MAX_SYNTH_LOG_LINES]
    ]


def _clip_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items[:MAX_SYNTH_FINDINGS]:
        for key, limit in (
            ("signal", SYNTH_SIGNAL_MAX),
            ("file", FILE_MAX),
            ("message", FINDING_MSG_MAX),
            ("hdl_file", FILE_MAX),
            ("hdl_function", SYNTH_SIGNAL_MAX),
        ):
            if isinstance(item.get(key), str):
                item[key] = clip(item[key], limit)
    return items[:MAX_SYNTH_FINDINGS]


def synthesize(
    name: str,
    frontend: str,
    run_dir: Path,
    clock_period_ns: float,
    stop_area_um2: float,
    deadline: float,
) -> dict[str, Any]:
    """Run both halves of the Yosys flow; the result is the `synth` part of the verb's result."""
    out_dir = run_dir / "out"
    sd = out_dir / SYNTH_DIR
    top = f"tt_um_{name}"
    reports = sd / synth_script.REPORTS
    reports.mkdir(parents=True, exist_ok=True)
    try:
        dont_use = synth_script.parse_cell_list(_read(Path(synth_script.NO_SYNTH_CELLS)))
        for cell in synth_script.parse_cell_list(_read(Path(synth_script.DRC_EXCLUDE_CELLS))):
            if cell not in dont_use:
                dont_use.append(cell)
        if not dont_use:
            raise RuntimeError("the do-not-use cell lists are missing from the image")
        (sd / synth_script.ABC_SCRIPT).write_text(synth_script.abc_area0_script(), encoding="utf-8")
        (sd / synth_script.ABC_SDC).write_text(synth_script.sdc_text(), encoding="utf-8")
        front = sd / "front.ys"
        back = sd / "back.ys"
        sources = ["project.v", f"{name}.v"]
        front.write_text(synth_script.front_script(sources, top, SYNTH_DIR), encoding="utf-8")
        back.write_text(
            synth_script.back_script(top, SYNTH_DIR, clock_period_ns, dont_use), encoding="utf-8"
        )
    except OSError as exc:
        raise RuntimeError(f"cannot prepare the synthesis folder ({exc})") from exc

    started = time.monotonic()

    def seconds() -> float:
        return round(time.monotonic() - started, 1)

    # 1. front half
    try:
        proc = _yosys(
            front, sd / synth_script.FRONT_LOG, out_dir, "front", deadline - time.monotonic()
        )
    except _Timeout:
        return _outcome(
            "timeout", yosys_seconds=seconds(), message="the generic synthesis step ran out of time"
        )
    if proc.returncode != 0:
        lines = _error_lines(proc, sd / synth_script.FRONT_LOG, name, out_dir)
        return _outcome(
            "yosys_failed",
            yosys_seconds=seconds(),
            log=lines,
            message="Yosys stopped while reading and optimizing the design",
        )
    generic_text = _read(reports / "generic.json")
    try:
        estimate_um2, generic_cells = synth_parse.estimate_generic_area(generic_text)
    except synth_parse.StatError as exc:
        return _outcome("yosys_failed", yosys_seconds=seconds(), message=str(exc))
    estimate = {
        "generic_area_um2": round(estimate_um2, 1),
        "generic_cells": generic_cells,
        "stop_area_um2": round(stop_area_um2, 1),
    }
    if estimate_um2 > stop_area_um2:
        return _outcome("too_big", yosys_seconds=seconds(), estimate=estimate)

    # 2. back half
    try:
        proc = _yosys(
            back, sd / synth_script.BACK_LOG, out_dir, "back", deadline - time.monotonic()
        )
    except _Timeout:
        return _outcome(
            "timeout",
            yosys_seconds=seconds(),
            estimate=estimate,
            message="the technology mapping step ran out of time",
        )
    if proc.returncode != 0:
        lines = _error_lines(proc, sd / synth_script.BACK_LOG, name, out_dir)
        return _outcome(
            "yosys_failed",
            yosys_seconds=seconds(),
            estimate=estimate,
            log=lines,
            message="Yosys stopped while mapping the design to sky130 cells",
        )
    netlist = sd / f"{top}.v"
    stat_text = _read(reports / "stat.json")
    try:
        stat = synth_parse.parse_stat(stat_text)
    except synth_parse.StatError as exc:
        return _outcome(
            "yosys_failed", yosys_seconds=seconds(), estimate=estimate, message=str(exc)
        )
    if not netlist.is_file() or netlist.is_symlink():
        return _outcome(
            "yosys_failed",
            yosys_seconds=seconds(),
            estimate=estimate,
            message="Yosys wrote no netlist",
        )

    # 3. what the reports say
    core_text = _read(out_dir / f"{name}.v", 1 << 20)
    latches = synth_parse.parse_latches(_read(reports / "latch.rpt"), name)
    if stat["latch_cells"] and not latches:
        latches = [{"signal": "", "file": "", "line": 0}]  # a latch the proc pass did not report
    checks = synth_parse.parse_checks(
        [
            _read(reports / "pre_synth_chk.rpt"),
            _read(reports / "generic_chk.rpt"),
            _read(reports / "chk.rpt"),
        ],
        name,
        core_text,
    )
    if frontend == "myhdl":
        myhdl_map.annotate(latches, core_text, name)
        myhdl_map.annotate(checks, core_text, name)
    stat_types: dict[str, int] = stat.pop("types")
    forbidden = synth_parse.forbidden_cells(stat_types, set(dont_use))
    return _outcome(
        "ran",
        yosys_seconds=seconds(),
        estimate=estimate,
        stat={
            k: stat[k] for k in ("cells", "flip_flops", "latch_cells", "combinational", "area_um2")
        },
        top_cells=[
            {"cell": clip(item["cell"], CELL_NAME_MAX), "count": item["count"]}
            for item in synth_parse.top_cells(stat_types)
        ],
        forbidden_cells=[clip(c, CELL_NAME_MAX) for c in forbidden[:MAX_FORBIDDEN_CELLS]],
        latches=_clip_findings(latches),
        checks=_clip_findings(checks),
        netlist=f"{SYNTH_DIR}/{top}.v",
        netlist_sha256=_sha256(netlist),
    )


def run(request: dict[str, Any]) -> dict[str, Any]:
    """The `synth` verb. Returns the emit result plus a `synth` entry (None unless emit was ok).

    Extra request fields: `clock_period_ns` (the spec's clock), `stop_area_um2` (the generic
    area above which the design is too big to bother with `abc`) and `timeout_s` (the host's
    time limit for the whole call).
    """
    started = time.monotonic()
    name, frontend, _author, run_dir = emit._check_request(request)
    clock_period_ns = _number(request, "clock_period_ns")
    stop_area_um2 = _number(request, "stop_area_um2")
    timeout_s = _number(request, "timeout_s")
    result = emit.emit(request)
    result["synth"] = None
    if not result.get("ok"):
        return result
    deadline = started + max(timeout_s - RESERVE_S, 1.0)
    synth = synthesize(name, frontend, run_dir, clock_period_ns, stop_area_um2, deadline)
    if isinstance(synth.get("message"), str):
        synth["message"] = clip(synth["message"], MESSAGE_MAX)
    result["synth"] = synth
    return result
