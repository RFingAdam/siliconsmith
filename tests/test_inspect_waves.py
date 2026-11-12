# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""inspect_waves (fake runner) and the pure parts of the worker's wave reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_emit_verilog import EmitRunner, make, outcome

from siliconsmith.result import summary_within_limits
from siliconsmith.runners.docker import ImageMissingError
from siliconsmith.tools.inspect_waves import inspect
from siliconsmith.worker import waves

PATH = "build/waves/icarus/tb.fst"


def wave_result(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "verb": "waves",
        "state": "ok",
        "format": "fst",
        "timescale": "1 ps",
        "columns": [{"name": "tb.uo_out", "width": 8}, {"name": "tb.clk", "width": 1}],
        "rows": [["0", "0x00", "0"], ["5", "0x01", "1"]],
        "rows_total": 2,
        "t_first_ns": "0",
        "t_last_ns": "5",
        "problems": [],
    }
    base.update(fields)
    return base


def go(tmp_path: Path, result: dict[str, Any] | None, **args: Any) -> Any:
    runner = EmitRunner(outcome(result))
    ctx, _ = make(tmp_path, "verilog", runner)
    params: dict[str, Any] = {
        "path": PATH,
        "signals": ["uo_out", "clk"],
        "start_ns": None,
        "end_ns": None,
        "max_rows": 50,
    }
    params.update(args)
    out = inspect(
        ctx,
        "chip",
        params["path"],
        params["signals"],
        params["start_ns"],
        params["end_ns"],
        params["max_rows"],
    )
    return out, runner


def test_table_and_request(tmp_path: Path) -> None:
    result, runner = go(tmp_path, wave_result(), start_ns=0, end_ns=10)
    assert result.ok and summary_within_limits(result.summary)
    lines = result.summary.splitlines()
    assert lines[0].startswith(
        "build/waves/icarus/tb.fst (FST, timescale 1 ps, 0 to 10 ns): 2 rows"
    )
    assert lines[1].split() == ["time_ns", "tb.uo_out", "tb.clk"]
    assert lines[2].split() == ["0", "0x00", "0"]
    assert result.details["rows"] == [["0", "0x00", "0"], ["5", "0x01", "1"]]
    assert result.details["truncated"] is False and result.next_hints == []
    ((verb, request, _, _),) = runner.calls
    assert verb == "waves"
    assert request == {
        "path": PATH,
        "signals": ["uo_out", "clk"],
        "start_ns": 0,
        "end_ns": 10,
        "max_rows": 50,
    }


def test_truncation_note_comes_first(tmp_path: Path) -> None:
    rows = [[str(n), f"0x{n:02x}", "0"] for n in range(50)]
    result, _ = go(tmp_path, wave_result(rows=rows, rows_total=276))
    assert result.details["truncated"] is True and result.details["rows_shown"] == 50
    assert "226 more rows not shown" in result.summary.splitlines()[0]
    assert summary_within_limits(result.summary)
    assert "max_rows" in result.next_hints[0]


def test_max_rows_is_capped_at_1000(tmp_path: Path) -> None:
    _, runner = go(tmp_path, wave_result(), max_rows=100000)
    assert runner.calls[0][1]["max_rows"] == 1000


def test_empty_range(tmp_path: Path) -> None:
    result, _ = go(tmp_path, wave_result(rows=[], rows_total=0))
    assert "No value changes in that range." in result.summary


@pytest.mark.parametrize(
    ("problems", "needle"),
    [
        (
            [{"signal": "uo_ot", "kind": "unknown", "matches": ["tb.uo_out"]}],
            "close matches: tb.uo_out",
        ),
        ([{"signal": "uo_ot", "kind": "unknown", "matches": []}], "no signal named 'uo_ot'"),
        ([{"signal": "x", "kind": "ambiguous", "matches": ["a.x", "b.x"]}], "matches several"),
    ],
)
def test_signal_problems_are_tool_errors(
    tmp_path: Path, problems: list[dict[str, Any]], needle: str
) -> None:
    with pytest.raises(ToolError, match=needle):
        go(tmp_path, wave_result(state="signal_problem", problems=problems))


@pytest.mark.parametrize(
    ("state", "needle"), [("missing", "run_tests with waves=True"), ("bad_format", "neither")]
)
def test_file_problems(tmp_path: Path, state: str, needle: str) -> None:
    with pytest.raises(ToolError, match=needle):
        go(tmp_path, wave_result(state=state))


@pytest.mark.parametrize(
    "path",
    [
        "spec.yaml",
        "build/waves/../../spec.yaml",
        "/etc/passwd",
        "build/waves/icarus/other.fst",
        "build/waves/modelsim/tb.fst",
        "build/waves/icarus/tb.fst/../../../x",
        "build\\waves\\icarus\\tb.fst",
        "build/.ss/x/sim/tb.fst",
    ],
)
def test_only_kept_wave_files_can_be_read(tmp_path: Path, path: str) -> None:
    runner = EmitRunner()
    ctx, _ = make(tmp_path, "verilog", runner)
    with pytest.raises(ToolError):
        inspect(ctx, "chip", path, ["uo_out"], None, None, 50)
    assert runner.calls == []


@pytest.mark.parametrize(
    "args",
    [
        {"signals": []},
        {"signals": ["a"] * 17},
        {"signals": ["bad name;"]},
        {"start_ns": 10, "end_ns": 5},
        {"max_rows": 0},
        {"start_ns": float("nan")},
    ],
)
def test_bad_arguments(tmp_path: Path, args: dict[str, Any]) -> None:
    with pytest.raises(ToolError):
        go(tmp_path, wave_result(), **args)


def test_malformed_results(tmp_path: Path) -> None:
    bad_results: list[dict[str, Any] | None] = [
        wave_result(rows=[["0", "0x00"]]),  # a row with the wrong number of cells
        wave_result(rows_total=1),
        wave_result(rows=[["0", "y" * 500_000]], columns=[{"name": "a", "width": 8}]),
        wave_result(format="gtkw"),
        wave_result(columns=[{"name": 3}]),
        {"ok": True, "state": "ok", "rows": "no"},
        {"ok": False, "error": {"kind": "ValueError", "message": "boom"}},
        None,
    ]
    for number, bad in enumerate(bad_results):
        folder = tmp_path / f"w{number}"
        folder.mkdir()
        with pytest.raises(ToolError):
            go(folder, bad)


def test_timeout_and_docker_errors(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(None, timed_out=True))
    ctx, _ = make(tmp_path, "verilog", runner)
    with pytest.raises(ToolError, match="took longer"):
        inspect(ctx, "chip", PATH, ["uo_out"], None, None, 50)
    runner = EmitRunner(error=ImageMissingError("docker pull x"))
    (tmp_path / "b").mkdir()
    ctx, _ = make(tmp_path / "b", "verilog", runner)
    with pytest.raises(ToolError, match="docker pull x"):
        inspect(ctx, "chip", PATH, ["uo_out"], None, None, 50)


# -- the worker's pure helpers -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "width", "is_bit", "text"),
    [
        (0, 1, True, "0"),
        (1, 1, True, "1"),
        ("x", 1, True, "x"),
        ("z", 1, True, "z"),
        (255, 8, False, "0xff"),
        (5, 12, False, "0x005"),
        (1, 4, False, "0x1"),
        ("xxxxxxxx", 8, False, "0xxx"),
        ("zzzz0001", 8, False, "0x"[:2] + "z1"),
        ("10x1", 4, False, "0xx"),
        ("101", 3, False, "0x5"),
        (1.5, None, False, "1.5"),
    ],
)
def test_format_value(value: Any, width: int | None, is_bit: bool, text: str) -> None:
    assert waves.format_value(value, width, is_bit) == text


NAMES = {
    n: object()
    for n in (
        "tb.uo_out",
        "tb.clk",
        "tb.user_project.uo_out",
        "tb.user_project.core.uo_out",
        "tb.user_project.core.count",
        "a.same",
        "b.same",
        "a.deeper.same",
    )
}


@pytest.mark.parametrize(
    ("asked", "found"),
    [
        ("tb.uo_out", "tb.uo_out"),
        ("uo_out", "tb.uo_out"),  # the match nearest the top wins
        ("user_project.uo_out", "tb.user_project.uo_out"),
        ("core.uo_out", "tb.user_project.core.uo_out"),
        ("count", "tb.user_project.core.count"),
        ("TOP.tb.clk", "tb.clk"),
        ("$rootio.tb.clk", "tb.clk"),
    ],
)
def test_resolve(asked: str, found: str) -> None:
    assert waves.resolve(asked, NAMES) == (found, None)


def test_resolve_problems() -> None:
    full, problem = waves.resolve("same", NAMES)
    assert full is None and problem is not None and problem["kind"] == "ambiguous"
    assert problem["matches"] == ["a.deeper.same", "a.same", "b.same"]
    full, problem = waves.resolve("uo_ot", NAMES)
    assert full is None and problem is not None and problem["kind"] == "unknown"
    assert "tb.uo_out" in problem["matches"]
    full, problem = waves.resolve("zzzzzzzz", NAMES)
    assert problem is not None and problem["matches"] == []


def test_sniff_format(tmp_path: Path) -> None:
    vcd = tmp_path / "a.fst"  # the name lies on purpose
    vcd.write_text("$date today $end\n$timescale 1ns $end\n")
    fst = tmp_path / "b.vcd"
    fst.write_bytes(b"\x00" + (329).to_bytes(8, "big") + b"\x00" * 400)
    junk = tmp_path / "c.fst"
    junk.write_bytes(b"hello")
    assert waves.sniff_format(vcd) == "vcd"
    assert waves.sniff_format(fst) == "fst"
    assert waves.sniff_format(junk) is None
    assert waves.sniff_format(tmp_path / "missing") is None


@pytest.mark.parametrize(
    "request_",
    [
        {"path": "x", "signals": ["a"], "max_rows": 5},
        {"path": "build/waves/icarus/tb.fst", "signals": [], "max_rows": 5},
        {"path": "build/waves/icarus/tb.fst", "signals": ["a"], "max_rows": 0},
        {"path": "build/waves/icarus/tb.fst", "signals": ["a"], "max_rows": True},
        {"path": "build/waves/icarus/tb.fst", "signals": ["a"], "max_rows": 5, "start_ns": "1"},
        {
            "path": "build/waves/icarus/tb.fst",
            "signals": ["a"],
            "max_rows": 5,
            "end_ns": float("inf"),
        },
    ],
)
def test_worker_checks_its_request(request_: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        waves.check_request(request_)


def test_a_killed_reader_is_reported_as_too_large(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(None, exit_code=137))
    ctx, _ = make(tmp_path, "verilog", runner)
    with pytest.raises(ToolError, match="too large to read"):
        inspect(ctx, "chip", PATH, ["uo_out"], None, None, 50)


def test_wide_bus_values_are_clipped() -> None:
    text = waves.format_value(2**1023, 1024, False)
    assert len(text) <= 80 and text.endswith("…")


class FakeSignal:
    """The part of pywellen's Signal the reader uses: len() and integer indexing."""

    def __init__(self, changes: list[tuple[int, Any]]) -> None:
        self.changes = changes
        self.reads = 0

    def __len__(self) -> int:
        return len(self.changes)

    def __getitem__(self, index: int) -> tuple[int, Any]:
        self.reads += 1
        return self.changes[index]


def test_window_changes_reads_only_the_window() -> None:
    signal = FakeSignal([(t, t % 256) for t in range(0, 1_000_000, 10)])
    before, changes = waves.window_changes(signal, 1.0, 500.0, 530.0)
    assert before == 490 % 256
    assert [t for t, _ in changes] == [500.0, 510.0, 520.0, 530.0]
    assert signal.reads < 100  # bisection plus the window, not the whole file
    assert waves.window_changes(FakeSignal([]), 1.0, 5.0, None) == (None, [])
    first = waves.window_changes(FakeSignal([(10, 1), (20, 2)]), 1.0, 5.0, None)
    assert first == (None, [(10.0, 1), (20.0, 2)])


def test_window_changes_caps_the_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(waves, "MAX_CHANGES_PER_SIGNAL", 3)
    _, changes = waves.window_changes(FakeSignal([(t, t) for t in range(10)]), 1.0, None, None)
    assert len(changes) == 3
