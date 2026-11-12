# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""RTL-versus-gate-level first difference: the `diff` entry of a failing gate-level run.

Both runs use the same testbench, tests and random seed, so the clock and the inputs are the
same; only the design under test differs. Only the edges AFTER reset is released are compared: the
RTL treats the clock's x-to-1 start at time zero as an edge, so a reset register is already 0
there, while the gate models stay unknown (x) until the first real edge. That difference is not
a bug in the design. The chip's outputs (`tb.uo_out`, `tb.uio_out`,
`tb.uio_oe`) are sampled once per rising edge of `tb.clk` in each wave file and compared. The
sample is the value in effect just BEFORE the edge (the last change strictly earlier than the
edge): the sky130 cell models delay a flop's output by 1 ns (`UNIT_DELAY=#1`), so a sample taken
at the edge itself would race. Cycle numbers count rising edges (0 to 1) from 1.

`first_difference` works on plain lists of `(time_ns, value)` and is tested without a simulator;
`load_trace` reads a wave file with pywellen (imported inside `waves._open`, in the image only).
Values are strings of `0`, `1`, `x`, `z`, one character per bit.
"""

from __future__ import annotations

import bisect
import itertools
from pathlib import Path
from typing import Any

from siliconsmith.worker import waves as wavefile

CLOCK = "tb.clk"
RESET = "tb.rst_n"
# cocotb starts every test after the first one a picosecond after the previous one ended, and a
# test that checks right after a rising edge ends at that edge: allow for both.
EDGE_SLACK_NS = 0.5
PINS = ("tb.uo_out", "tb.uio_out", "tb.uio_oe")
# The inputs the test drives: an x on one of them explains an x on the outputs.
INPUTS = ("tb.ui_in", "tb.uio_in", "tb.ena")
MAX_CYCLES = 20_000
MAX_CHANGES = 200_000
MAX_WAVE_BYTES = 64 * 1024 * 1024
MAX_SIGNALS_SHOWN = 3

Trace = dict[str, list[tuple[float, str]]]


def bit_string(value: Any, width: int | None) -> str | None:
    """A pywellen value as a string of 0/1/x/z (None for a value that is not a bit vector)."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        return format(value, f"0{width or 1}b")[-(width or 1) :]
    if not isinstance(value, str):
        return None
    text = value.lower().replace("u", "x").replace("w", "x").replace("-", "x")
    if not text or not set(text) <= set("01xzlh"):
        return None
    text = text.replace("l", "0").replace("h", "1")
    if width is not None and len(text) < width:  # a VCD value is cut short at the left
        text = (text[0] if text[0] in "xz" else "0") * (width - len(text)) + text
    return text


def load_trace(path: Path, ticks_limit_ns: float | None = None) -> Trace:
    """The clock and the output pins from the wave file: `{name: [(time_ns, bits), ...]}`.

    A signal that is missing from the file is missing from the result. At most MAX_CHANGES
    changes per signal are read, and nothing after `ticks_limit_ns`.
    """
    wave = wavefile._open(str(path))
    scale = wavefile._UNIT_NS.get(str(wave.timescale.unit))
    if scale is None:
        return {}
    tick_ns = float(wave.timescale.factor) * scale
    names: dict[str, Any] = {}
    for var in wave.all_vars():
        names.setdefault(wavefile._normalize(str(var.full_name)), var)
    trace: Trace = {}
    for wanted in (CLOCK, RESET, *INPUTS, *PINS):
        full, problem = wavefile.resolve(wanted, names)
        if problem is not None or full is None:
            continue
        var = names[full]
        width = var.bitwidth if isinstance(var.bitwidth, int) else None
        signal = var.tv
        series: list[tuple[float, str]] = []
        for index in range(min(len(signal), MAX_CHANGES)):
            time, value = signal[index]
            time_ns = time * tick_ns
            if ticks_limit_ns is not None and time_ns > ticks_limit_ns:
                break
            bits = bit_string(value, width)
            if bits is not None:
                series.append((time_ns, bits))
        trace[wanted] = series
    return trace


def rising_edges(clock: list[tuple[float, str]]) -> list[float]:
    """Times of the 0 to 1 changes of a one-bit clock (an x to 1 at time zero is not an edge)."""
    edges = []
    for (_t0, before), (t1, after) in itertools.pairwise(clock):
        if before == "0" and after == "1":
            edges.append(t1)
    return edges


def first_difference(
    rtl: Trace, gl: Trace, stop_ns: float | None = None, max_cycles: int = MAX_CYCLES
) -> dict[str, Any]:
    """Compare the pins of two traces at every rising edge up to `stop_ns`.

    Result keys: `state` (`differs`, `same`, `no_clock`, `no_pins`), `cycles_compared`, and for
    `differs` also `cycle` (1 for the first rising edge), `time_ns` (the edge), and `signals`: the
    pins that differ at that first cycle, each `{signal, rtl, gl}`. Edges where `tb.rst_n` (if it
    is in the trace and was driven) was not 1 just before the edge are not compared (counted in
    `edges_skipped`), but they are counted in the cycle numbers. `no_edge`: no edge was compared
    (the failure came before the first rising edge after reset; `reset_low` if rst_n never rose).
    `same` with `truncated`: the edges up to `cycle_limit` match, later ones were not compared.
    `input_unknown` (differs): the first of tb.ui_in/uio_in/ena that had an x or z bit just
    before a compared edge up to the difference. `reset_never_low` (differs): rst_n is in the
    trace but was never 0 before the difference.
    `gl_known_before` (differs): the first differing pin had a fully known gate-level value at
    an earlier compared edge.
    """
    clock = rtl.get(CLOCK)
    pins = [pin for pin in PINS if pin in rtl and pin in gl]
    if not clock:
        return {"state": "no_clock", "cycles_compared": 0}
    edges = rising_edges(clock)
    if not edges:
        return {"state": "no_edge", "cycles_compared": 0}
    if not pins:
        return {"state": "no_pins", "cycles_compared": 0}
    times = {pin: ([t for t, _ in rtl[pin]], [t for t, _ in gl[pin]]) for pin in pins}
    reset = rtl.get(RESET)
    reset_times = [t for t, _ in reset] if reset else []
    # A test that never drives rst_n has no reset to wait for (and no time-zero artefact: that
    # needs rst_n to be 0 at time zero), so then every edge is compared.
    held = [
        bits
        for t, bits in (reset or [])
        if bits in ("0", "1") and (stop_ns is None or t <= stop_ns)
    ]
    wait_for_reset = bool(held)
    compared = 0
    skipped = 0
    inputs = [
        (pin, trace, [t for t, _ in trace[pin]])
        for trace in (rtl, gl)
        for pin in INPUTS
        if pin in trace
    ]
    unknown_input: str | None = None
    gl_known: set[str] = set()  # pins whose gate-level value was fully known at an earlier edge
    for cycle, edge in enumerate(edges, start=1):
        if stop_ns is not None and edge > stop_ns:
            break
        if cycle > max_cycles:
            return {
                "state": "same",
                "cycles_compared": compared,
                "edges_skipped": skipped,
                "truncated": True,
                "cycle_limit": max_cycles,
            }
        if wait_for_reset and _before(reset or [], reset_times, edge) != "1":
            skipped += 1
            continue  # reset is still held: the gates may not have settled yet
        compared += 1
        differing = []
        for pin in pins:
            a = _before(rtl[pin], times[pin][0], edge)
            b = _before(gl[pin], times[pin][1], edge)
            if a != b:
                differing.append(
                    {"signal": pin.removeprefix("tb."), "rtl": a or "-", "gl": b or "-"}
                )
            elif b is not None and not set(b) & set("xz"):
                gl_known.add(pin)
        if unknown_input is None:
            for pin, trace, trace_times in inputs:
                bits = _before(trace[pin], trace_times, edge)
                if bits is not None and set(bits) & set("xz"):
                    unknown_input = pin.removeprefix("tb.")
                    break
        if differing:
            first_pin = "tb." + differing[0]["signal"]
            return {
                "state": "differs",
                "cycle": cycle,
                "time_ns": round(edge, 3),
                "cycles_compared": compared,
                "edges_skipped": skipped,
                "gl_known_before": first_pin in gl_known,
                "input_unknown": unknown_input,
                "reset_never_low": bool(reset)
                and not any(bits == "0" and t < edge for t, bits in reset or []),
                "signals": differing[:MAX_SIGNALS_SHOWN],
            }
    if compared == 0:
        # `reset_low`: rst_n was driven but never reached 1 before the failure.
        return {
            "state": "no_edge",
            "cycles_compared": 0,
            "reset_low": wait_for_reset and "1" not in held,
        }
    return {"state": "same", "cycles_compared": compared, "edges_skipped": skipped}


def _before(series: list[tuple[float, str]], times: list[float], edge: float) -> str | None:
    index = bisect.bisect_left(times, edge)
    return series[index - 1][1] if index > 0 else None


def failing_end_ns(tests: list[dict[str, Any]]) -> float | None:
    """When the first failing test ended: the sim time of the tests up to and including it.

    cocotb runs the tests one after the other in one simulation, so a test's `sim_time_ns` is its
    own length. None if a time is missing (then the whole trace is compared).
    """
    total = 0.0
    for test in tests:
        duration = test.get("sim_time_ns")
        if not isinstance(duration, int | float):
            return None
        total += float(duration)
        if test.get("outcome") in ("fail", "error"):
            return total
    return None


def compare_files(rtl_path: Path, gl_path: Path, stop_ns: float | None) -> dict[str, Any]:
    """The first difference between two wave files (see `first_difference`)."""
    for path in (rtl_path, gl_path):
        if path.stat().st_size > MAX_WAVE_BYTES:
            return {"state": "too_big", "cycles_compared": 0}
    limit = None if stop_ns is None else stop_ns + 2 * EDGE_SLACK_NS
    stop = None if stop_ns is None else stop_ns + EDGE_SLACK_NS
    return first_difference(
        load_trace(rtl_path, limit), load_trace(gl_path, limit), stop, MAX_CYCLES
    )
