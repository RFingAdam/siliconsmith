# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""worker/gldiff.py: the RTL-versus-gate-level first difference, on small synthetic traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from siliconsmith.worker import gldiff
from siliconsmith.worker import waves as wavefile

Trace = gldiff.Trace


def clock(periods: int, half: float = 5.0) -> list[tuple[float, str]]:
    """tb.clk as cocotb drives it: x, then 1 at 0, then toggling; first 0 to 1 edge at 10 ns."""
    series = [(0.0, "x"), (0.0, "1")]
    for i in range(periods):
        series += [((2 * i + 1) * half, "0"), ((2 * i + 2) * half, "1")]
    return series


def traces(
    rtl_out: list[tuple[float, str]], gl_out: list[tuple[float, str]]
) -> tuple[Trace, Trace]:
    rtl: Trace = {gldiff.CLOCK: clock(6), "tb.uo_out": rtl_out, "tb.uio_oe": [(0.0, "00000000")]}
    gl: Trace = {gldiff.CLOCK: clock(6), "tb.uo_out": gl_out, "tb.uio_oe": [(0.0, "00000000")]}
    return rtl, gl


def test_bit_strings_keep_x_and_z_and_pad_like_a_vcd() -> None:
    assert gldiff.bit_string(5, 8) == "00000101"
    assert gldiff.bit_string("1xz", 8) == "00000" + "1xz"
    assert gldiff.bit_string("x", 4) == "xxxx"
    assert gldiff.bit_string("z1", 4) == "zzz1"
    assert gldiff.bit_string(1.5, 8) is None
    assert gldiff.bit_string("hello", 8) is None


def test_rising_edges_ignore_the_x_to_1_at_time_zero() -> None:
    assert gldiff.rising_edges(clock(3)) == [10.0, 20.0, 30.0]


def test_the_first_difference_is_sampled_just_before_the_edge() -> None:
    # RTL changes AT each edge (10, 20, ...); the gates 1 ns later (UNIT_DELAY). Sampled before
    # the edge they agree, so a one-nanosecond skew is not a difference.
    rtl = [(0.0, "00000000"), (10.0, "00000001"), (20.0, "00000010"), (30.0, "00000011")]
    gl = [
        (0.0, "xxxxxxxx"),  # unknown for the first nanosecond only
        (1.0, "00000000"),
        (11.0, "00000001"),
        (21.0, "00000010"),
        (31.0, "00000011"),
    ]
    a, b = traces(rtl, gl)
    assert gldiff.first_difference(a, b) == {"state": "same", "cycles_compared": 6}


def test_an_unknown_start_is_reported_at_the_first_edge() -> None:
    rtl = [(0.0, "00000000"), (10.0, "00000001")]
    gl = [(0.0, "xxxxxxxx")]
    a, b = traces(rtl, gl)
    found = gldiff.first_difference(a, b)
    assert found["state"] == "differs"
    assert found["cycle"] == 1 and found["time_ns"] == 10.0
    assert found["signals"] == [{"signal": "uo_out", "rtl": "00000000", "gl": "xxxxxxxx"}]


def test_the_cycle_number_counts_rising_edges() -> None:
    rtl = [(0.0, "00000000"), (10.0, "00000001"), (20.0, "00000010"), (30.0, "00000011")]
    gl = [(0.0, "00000000"), (11.0, "00000001"), (21.0, "00000010"), (31.0, "00000111")]
    a, b = traces(rtl, gl)
    found = gldiff.first_difference(a, b)
    # Edge 4 (t=40) is the first one that shows the wrong value, sampled before the edge.
    assert (found["cycle"], found["time_ns"]) == (4, 40.0)
    assert found["signals"] == [{"signal": "uo_out", "rtl": "00000011", "gl": "00000111"}]


def test_nothing_after_the_failure_is_compared() -> None:
    rtl = [(0.0, "00000000")]
    gl = [(0.0, "00000000"), (41.0, "00000001")]
    a, b = traces(rtl, gl)
    assert gldiff.first_difference(a, b, stop_ns=25.0) == {"state": "same", "cycles_compared": 2}
    assert gldiff.first_difference(a, b)["state"] == "differs"


def test_a_pin_that_differs_with_another_is_listed_after_it() -> None:
    a, b = traces([(0.0, "00000000")], [(0.0, "00000001")])
    b["tb.uio_oe"] = [(0.0, "11111111")]
    found = gldiff.first_difference(a, b)
    assert [s["signal"] for s in found["signals"]] == ["uo_out", "uio_oe"]


def test_a_trace_without_a_clock_or_pins_is_not_compared() -> None:
    a, b = traces([(0.0, "0")], [(0.0, "0")])
    assert gldiff.first_difference({**a, gldiff.CLOCK: []}, b)["state"] == "no_clock"
    assert gldiff.first_difference({gldiff.CLOCK: clock(2)}, {})["state"] == "no_pins"


def test_the_failing_end_adds_up_the_test_durations() -> None:
    tests: list[dict[str, Any]] = [
        {"outcome": "pass", "sim_time_ns": 100.0},
        {"outcome": "fail", "sim_time_ns": 40.0},
        {"outcome": "pass", "sim_time_ns": 500.0},
    ]
    assert gldiff.failing_end_ns(tests) == 140.0
    assert gldiff.failing_end_ns([{"outcome": "pass", "sim_time_ns": 1.0}]) is None
    assert gldiff.failing_end_ns([{"outcome": "fail", "sim_time_ns": None}]) is None


class FakeVar:
    def __init__(self, full_name: str, width: int, changes: list[tuple[int, Any]]) -> None:
        self.full_name, self.bitwidth, self.tv = full_name, width, changes


class FakeWave:
    class timescale:
        factor, unit = 1, "ps"

    def __init__(self, variables: list[FakeVar]) -> None:
        self._vars = variables

    def all_vars(self) -> list[FakeVar]:
        return self._vars


def test_load_trace_reads_pywellen_style_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    wave = FakeWave(
        [
            FakeVar("tb.clk", 1, [(0, 0), (5000, 1), (10000, 0)]),
            FakeVar("tb.uo_out", 8, [(0, "xxxxxxxx"), (1000, 3)]),
            FakeVar("tb.user_project.uo_out", 8, [(0, 9)]),  # the nearest one to the top wins
        ]
    )
    monkeypatch.setattr(wavefile, "_open", lambda path: wave)
    trace = gldiff.load_trace(Path("x.fst"))
    assert trace["tb.clk"] == [(0.0, "0"), (5.0, "1"), (10.0, "0")]
    assert trace["tb.uo_out"] == [(0.0, "xxxxxxxx"), (1.0, "00000011")]
    assert "tb.uio_out" not in trace
    short = gldiff.load_trace(Path("x.fst"), ticks_limit_ns=6.0)
    assert short["tb.clk"] == [(0.0, "0"), (5.0, "1")]


def test_compare_files_refuses_a_huge_wave_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small, big = tmp_path / "a.fst", tmp_path / "b.fst"
    small.write_bytes(b"x")
    big.write_bytes(b"x")
    monkeypatch.setattr(gldiff, "MAX_WAVE_BYTES", 0)
    assert gldiff.compare_files(small, big, None)["state"] == "too_big"
