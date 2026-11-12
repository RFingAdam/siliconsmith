# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`inspect_waves`: read a wave file kept by `run_tests` and show a compact table of changes.

The file is read in the container by the worker's `waves` verb (pywellen), never on the host.
The result comes back as untrusted data and is validated with pydantic. Only files under
`build/waves/` can be read.
"""

from __future__ import annotations

import asyncio
import math
import re
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from siliconsmith.context import ServerContext
from siliconsmith.paths import project_lock, validate_relpath
from siliconsmith.result import ToolResult
from siliconsmith.runners.docker import ToolchainError, remove_run_dirs
from siliconsmith.tools._common import get_project
from siliconsmith.worker import limits as lim

WAVES_TIMEOUT_S = 120.0
DEFAULT_MAX_ROWS = 50
WAVE_FILE_RE = re.compile(r"^build/waves/(icarus|verilator)/tb\.(fst|vcd)$")
SIGNAL_RE = re.compile(r"^[A-Za-z_$\\][\w$.\[\]:\\ -]{0,299}$")


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class _Column(_Strict):
    name: str = Field(max_length=lim.WAVE_SIGNAL_MAX)
    width: int | None = Field(default=None, ge=1)


class _Problem(_Strict):
    signal: str = Field(max_length=lim.WAVE_SIGNAL_MAX)
    kind: Literal["unknown", "ambiguous"]
    matches: list[str] = Field(default_factory=list, max_length=lim.MAX_SUGGESTIONS)


class _WavesResult(_Strict):
    state: Literal["ok", "missing", "bad_format", "signal_problem"]
    format: Literal["fst", "vcd"] | None = None
    timescale: str | None = Field(default=None, max_length=40)
    columns: list[_Column] = Field(default_factory=list, max_length=lim.MAX_WAVE_SIGNALS)
    rows: list[list[Annotated[str, Field(max_length=lim.WAVE_VALUE_MAX)]]] = Field(
        default_factory=list, max_length=lim.MAX_WAVE_ROWS
    )
    rows_total: int = Field(default=0, ge=0)
    t_first_ns: str | None = Field(default=None, max_length=40)
    t_last_ns: str | None = Field(default=None, max_length=40)
    problems: list[_Problem] = Field(default_factory=list, max_length=lim.MAX_WAVE_SIGNALS)


def _check_args(
    path: str,
    signals: list[str],
    start_ns: int | float | None,
    end_ns: int | float | None,
    max_rows: int,
) -> str:
    rel = path.strip()
    try:
        validate_relpath(rel)
    except ToolError as exc:
        raise ToolError(f"{exc} (wave files are under build/waves/)") from exc
    if not WAVE_FILE_RE.fullmatch(rel):
        raise ToolError(
            f"{path!r} is not a wave file that run_tests keeps; use the path from its result "
            "(build/waves/icarus/tb.fst or build/waves/verilator/tb.fst), after "
            "run_tests with waves=True"
        )
    if not 1 <= len(signals) <= lim.MAX_WAVE_SIGNALS:
        raise ToolError(f"give 1 to {lim.MAX_WAVE_SIGNALS} signal names, for example ['uo_out']")
    for name in signals:
        if not SIGNAL_RE.fullmatch(name):
            raise ToolError(f"{name!r} is not a signal name (use names like 'uo_out' or 'tb.clk')")
    for label, value in (("start_ns", start_ns), ("end_ns", end_ns)):
        if value is not None and not math.isfinite(value):
            raise ToolError(f"{label} must be a finite number of nanoseconds")
    if start_ns is not None and end_ns is not None and start_ns > end_ns:
        raise ToolError("start_ns is after end_ns")
    if max_rows < 1:
        raise ToolError("max_rows must be at least 1")
    return rel


def _problem_error(problems: list[_Problem]) -> ToolError:
    lines = []
    for item in problems:
        if item.kind == "ambiguous":
            lines.append(
                f"{item.signal!r} matches several signals: {', '.join(item.matches)}; "
                "give more of the hierarchical name"
            )
        else:
            near = f"; close matches: {', '.join(item.matches)}" if item.matches else ""
            lines.append(f"no signal named {item.signal!r}{near}")
    return ToolError("; ".join(lines))


def _table(columns: list[_Column], rows: list[list[str]]) -> list[str]:
    heads = ["time_ns", *(c.name for c in columns)]
    widths = [len(h) for h in heads]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=False)]

    def fmt(cells: list[str]) -> str:
        return "  ".join(c.rjust(w) for c, w in zip(cells, widths, strict=False))

    return [fmt(heads), *(fmt(row) for row in rows)]


def inspect(
    ctx: ServerContext,
    project_name: str,
    path: str,
    signals: list[str],
    start_ns: int | float | None,
    end_ns: int | float | None,
    max_rows: int,
) -> ToolResult:
    project = get_project(ctx, project_name)
    rel = _check_args(path, signals, start_ns, end_ns, max_rows)
    max_rows = min(max_rows, lim.MAX_WAVE_ROWS)
    request: dict[str, Any] = {
        "path": rel,
        "signals": signals,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "max_rows": max_rows,
    }
    with project_lock(project.dir):
        try:
            try:
                outcome = ctx.get_runner().run(
                    "waves", request, project_dir=project.dir, timeout_s=WAVES_TIMEOUT_S
                )
            except ToolchainError as exc:
                raise ToolError(str(exc)) from exc
        finally:
            remove_run_dirs(project.dir / "build")
    if outcome.timed_out:
        raise ToolError(
            f"reading {rel} took longer than {WAVES_TIMEOUT_S:.0f} s; ask for a shorter time "
            "range or fewer signals, or run run_tests with a test_filter for one short test"
        )
    raw = outcome.result
    if raw is None and outcome.exit_code in (137, -9):
        raise ToolError(
            f"the wave file {rel} is too large to read in one go (the reader ran out of memory); "
            "ask for a short time range (start_ns and end_ns) and fewer signals, or run "
            "run_tests with a test_filter for one short test"
        )
    if raw is None or raw.get("error") is not None:
        detail = ""
        if raw and isinstance(raw.get("error"), dict):
            err = raw["error"]
            detail = f" ({str(err.get('kind'))[:100]}: {str(err.get('message'))[:300]})"
        raise ToolError(
            f"the wave reader failed inside the container{detail}. Call toolchain_info to check "
            "that the toolchain image matches this version of Siliconsmith."
        )
    try:
        result = _WavesResult.model_validate(raw)
    except ValidationError:
        raise ToolError(
            "the wave reader returned an unreadable result; call toolchain_info to check the "
            "toolchain image"
        ) from None
    if result.state == "missing":
        raise ToolError(
            f"{rel} does not exist; call run_tests with waves=True (and the same simulator) first"
        )
    if result.state == "bad_format":
        raise ToolError(f"{rel} is neither a VCD nor an FST wave file")
    if result.state == "signal_problem":
        raise _problem_error(result.problems)
    if result.rows_total < len(result.rows) or any(
        len(row) != len(result.columns) + 1 for row in result.rows
    ):
        raise ToolError("the wave reader returned a table that does not add up")
    shown = len(result.rows)
    truncated = result.rows_total > shown
    window = ""
    if start_ns is not None or end_ns is not None:
        first = "start" if start_ns is None else start_ns
        last = "end" if end_ns is None else end_ns
        window = f", {first} to {last} ns"
    head = (
        f"{rel} ({(result.format or '?').upper()}, timescale {result.timescale}{window}): "
        f"{result.rows_total} rows where a signal changed"
    )
    if truncated:
        head += (
            f"; showing the first {shown} (max_rows={max_rows}). "
            f"{result.rows_total - shown} more rows not shown: narrow start_ns/end_ns or raise "
            f"max_rows (up to {lim.MAX_WAVE_ROWS})."
        )
    lines = [head]
    if not result.rows:
        lines.append("No value changes in that range.")
    else:
        lines += _table(result.columns, result.rows)
    details: dict[str, Any] = {
        "path": rel,
        "format": result.format,
        "timescale": result.timescale,
        "columns": [c.model_dump() for c in result.columns],
        "rows": result.rows,
        "rows_total": result.rows_total,
        "rows_shown": shown,
        "truncated": truncated,
        "max_rows": max_rows,
        "start_ns": start_ns,
        "end_ns": end_ns,
        "t_first_ns": result.t_first_ns,
        "t_last_ns": result.t_last_ns,
    }
    hints = []
    if truncated:
        hints.append(
            f"Call inspect_waves again with start_ns/end_ns around the time you care about, or "
            f"max_rows up to {lim.MAX_WAVE_ROWS}."
        )
    return ToolResult.from_lines(True, lines, details=details, next_hints=hints)


DESCRIPTION = """\
Show how signals changed in a wave file that run_tests kept (run it with waves=True; the result \
gives the path, build/waves/<simulator>/tb.fst). signals are names like 'uo_out' or \
'user_project.uo_out' (a suffix of the full hierarchical name; the match nearest the top wins). \
start_ns and end_ns limit the time range. The table has one row per time where a requested \
signal changed: hex for buses, 0/1 for bits, x/z kept. At most max_rows rows come back \
(default 50, hard limit 1000); a note says how many more there are. details.rows has every \
returned row as strings.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="inspect_waves",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
    )
    async def inspect_waves(
        project: str,
        path: str,
        signals: list[str],
        start_ns: float | None = None,
        end_ns: float | None = None,
        max_rows: int = DEFAULT_MAX_ROWS,
    ) -> ToolResult:
        return await asyncio.to_thread(
            inspect, ctx, project, path, signals, start_ns, end_ns, max_rows
        )
