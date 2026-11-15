# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The equivalence check: does the sky130 netlist do what the RTL does? (PLAN.md M1-3)

Two entry points, both stdlib only and both run inside the container:

* `run(request)`: the `equiv` verb. Emit (same as `emit_verilog`; lint stops it), then the
  PRODUCTION synthesis of `worker/synth.py` (same script, early stop and checks as `synthesize`;
  the netlist stays in the run folder), then `check` on that netlist file. There is no way to hand
  this verb a different netlist.
* `check(rtl_dir, name, netlist, scratch)`: the check itself. Tests call it (through a test-only
  script, in the container) on a hand-edited netlist.

The Yosys scripts are in `equiv_script.py`, the log readers in `equiv_parse.py`; the stages and
their meaning are described in the first. This module runs them in order, enforces the time limits
and turns what they find into one verdict.

## What is promised

PROVEN: the netlist implements the RTL as Yosys reads it, once comments, attributes and macros are
out of the way (synthesis did not change the logic; an `x` in the RTL is a don't-care). BEST
EFFORT: `equiv_sv.refusal` refuses (`not_proven`, reason `guard_sim_synth_differ`) the common
constructs that a simulator and Yosys read differently, `_hidden_lint` a race or x warning
that a comment hid from the emit lint (the RTL linted again without its comments), and
`_tree_checks` what is judged on Verilator's syntax tree (an impure function, a combinational
block that can read a signal before it writes it on some path, a parameter with an x or z bit, a
parameter value that the simulator Icarus works out differently, or to the same value with
another width or sign (an untyped `8'hff + 8'h00` is 9 bits wide in Icarus, 8 in Verilator and
the gates), a different elaboration, a call with constant arguments of a function that selects
at a computed index not proven inside the range or divides by a value not proven nonzero
(`equiv_tree.constant_call`), a constant function argument or cast operand narrower than the
input or cast that can carry, which Yosys 0.62 works out at its own width
(`equiv_tree.narrow_argument`), a select outside the range that Yosys finds, also in an arm of
`?:` that a constant condition never selects, RTL that Icarus cannot compile or stops on). NOT
detected: an index computed from a signal or a variable (a loop index passed into a function
too) that goes outside the range or a division by a signal that is 0, a constant that is x to a
simulator steering a case inside a block, the values inside a generate loop or module copy, an
inline call of a function that leaves its result unset for some constant arguments, a constant
expression that Yosys evaluates differently from a simulator (a size cast or an unbased `'1`
inside a localparam, `$rtoi` in any constant expression: a localparam or a generate condition; a
real parameter override, which Yosys passes to the instance with six decimals; a finite real
mixed into a signal expression, `ui_in == 2.5`; a select outside the range inside a constant
function, also in a parameter's value, which Yosys 0.62 evaluates to an undefined value, so the
gates can differ even where Icarus and Verilator agree and the verdict can change with unrelated
edits; only a parameter value on which the simulator and Verilator disagree is refused, as an
untyped localparam that overflows is, which Icarus makes wider; `$bits` is unsigned in Icarus and
signed in Verilator and Yosys, so `$bits(x) > -1` differs), an untyped localparam that Verilator
and the gates make exactly 32 bits wide and Icarus wider, computed with an unsized number or with
sized 32-bit numbers (`W - 1` and `32'hffff_ffff + 32'h0` have 33 bits there: a concatenation,
`$bits`, `^` or an expression that overflows 32 bits sees it; `equiv_icarus.shape_disagreement`
lets it pass), and anything else that depends on simulation event order;
gate-level simulation (`run_tests` with `gl=true`) is the check against simulation behaviour.

## Verdict

`check` returns one dict (every text field is clipped, see `worker/limits.py`):

    verdict      equivalent | not_equivalent | not_proven | error. `ok` is true ONLY for equivalent.
    ok           bool
    reason       None for equivalent; else a machine code: rtl_initial_value,
                 initial_values, power_up_dependent, unreadable_counterexample, base_case,
                 unproven_points, unpaired_outputs, time_limit, guard_clocking, guard_tristate,
                 guard_async_set_reset, guard_sim_synth_differ, guard_include, state_check_unusable,
                 structure, cells,
                 netlist_modules, xz_constant,
    method       "equiv_induct" | "k-induction length L" | None
    message      one or two plain-English sentences; not_proven ends with "This is NOT a pass."
    seconds      {tree, elab, stage1, initdep, base, stage4, total}: seconds per stage that ran
                 (tree: Verilator's syntax tree and Icarus's elaboration, elab: Yosys's
                 elaboration of the RTL)
    stage1       {status, proven, unproven, unproven_signals, unpaired_outputs} or None
    base         {status, run, cycle, differs} or None
    first_mismatch  {cycle, differs: [{signal, rtl, netlist}]} or None. Cycle 1 is power-up with
                 rst_n low; outputs are compared from cycle 2. Cycle 1 is compared only when
                 stage 1 proved every point and paired every output: the X run of the base case
                 compares there every value the RTL defines, for every power-up state of the
                 netlist's flip-flops (an output that differs is reported at cycle 1). The
                 k-induction path (stage 1 did not prove everything) compares nothing at cycle
                 1. A design without flip-flops needs no cycle 1: from cycle 2 its inputs take
                 every value.
    trace        per cycle up to the mismatch: {cycle, inputs{rst_n, ui_in, uio_in},
                 rtl{uo_out, uio_out, uio_oe}, netlist{...}} as bit strings (x = unknown)
    internal_inconsistency  true when the induction and the referee disagree
    cycles_covered  cycles from reset searched without a difference (not_proven)
    time_limit_hit  true when a Yosys step was stopped at its time limit
    detail       a few Yosys error lines (error and guard refusals)
    netlist_sha256  sha256 of the netlist bytes that were checked (the private copy)

Time: every Yosys call is a subprocess with its own limit (`budget_s`, 120 s; Yosys's own
`sat -timeout` is ignored), and the whole check has a deadline (`deadline_s`, 300 s). The two
searches from reset of stage 4 (the one-shot and the step-by-step search) run AT ONCE, as a race
(`race_yosys`, `_per_side`), each with the time one call may take; the first answer that decides
stops the other. Each call runs in the private `scratch` folder, which holds copies of the
inputs, so parallel checks never share files.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from siliconsmith.worker import (
    emit,
    equiv_elab,
    equiv_icarus,
    equiv_parse,
    equiv_script,
    equiv_sv,
    equiv_tree,
    lint,
    myhdl_map,
    synth,
)
from siliconsmith.worker.limits import (
    EQUIV_DETAIL_LINE_MAX,
    EQUIV_SIGNAL_MAX,
    EQUIV_VALUE_MAX,
    MAX_EQUIV_DETAIL,
    MAX_EQUIV_DIFFS,
    MAX_EQUIV_TRACE,
    MESSAGE_MAX,
    clip,
)

SEQ = 5  # induction length of equiv_simple / equiv_induct (and the base case: SEQ + 2 cycles)
DEPTH = 32  # cycles of the referee from reset
BUDGET_S = 120.0  # per Yosys call
DEADLINE_S = 300.0  # the whole check
# How often the race of the two searches from reset (stage 4, `race_yosys`) looks whether one
# has ended.
POLL_S = 0.05
NETLIST_MAX_BYTES = 16 * 1024 * 1024
NOT_A_PASS = "This is NOT a pass."
# Why a base case or a check from reset runs out of time, and what helps (in the message).
SOLVER_HINT = (
    "arithmetic such as multipliers, multiply-accumulate, or CRC/XOR chains mixed with adders is "
    "hard for the solver; a smaller width or splitting the logic can help"
)
EQUIV_DIR = "equiv"

# A Yosys runner: (folder, script text, tag, time limit in seconds) -> (exit code, log text).
# Exit code None means the call was stopped at the limit.
YosysRunner = Callable[[Path, str, str, float], tuple[int | None, str]]
# A Yosys race: (folder, [(script text, tag)], time limit in seconds for each) -> a generator of
# (tag, exit code, log text), one per script as it ends (exit code None: stopped at the limit).
# Closing the generator stops the scripts that still run.
YosysRace = Callable[
    [Path, list[tuple[str, str]], float], Generator[tuple[str, int | None, str], None, None]
]


def _yosys_argv(tag: str) -> list[str]:
    """The command that runs the script `<tag>.ys` with its log in `<tag>.log`."""
    return ["yosys", "-q", "-l", f"{tag}.log", "-s", f"{tag}.ys"]


def run_yosys(cwd: Path, script: str, tag: str, timeout: float) -> tuple[int | None, str]:
    """Run one Yosys script in `cwd`; the log is `<tag>.log` next to it. Stops at `timeout`.
    It is a race of one (`race_yosys`): the Yosys runs in a session and process group of its
    own, and at the limit that whole group is killed and reaped (the `yosys` command is a
    wrapper that starts the real one, so killing only the process it starts would leave the
    solver running); nothing else in the container is touched."""
    runs = race_yosys(cwd, [(script, tag)], timeout)
    with contextlib.closing(runs):
        for _, code, log in runs:
            return code, log
    return None, ""  # unreachable: a race reports every script


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Kill a script's whole process group (it was started in a session of its own) and reap
    it: nothing it started keeps running."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def race_yosys(
    cwd: Path, jobs: list[tuple[str, str]], timeout: float
) -> Generator[tuple[str, int | None, str], None, None]:
    """Run several Yosys scripts AT ONCE in `cwd` (`jobs`: (script text, tag); the log of each
    is `<tag>.log`, as in `run_yosys`), each with the time limit `timeout`, and yield
    `(tag, exit code, log text)` for each as it ends; a script still running at the limit is
    killed and yields exit code None. Each script runs in a session and process group of its
    own; when the caller closes the generator (it has its answer) every script still running is
    killed with its whole group and reaped, so no solver keeps running. Only Yosys runs here
    (design code was reaped after elaboration), so nothing else in the container is touched."""
    running: dict[str, tuple[subprocess.Popen[bytes], Any]] = {}
    try:
        for script, tag in jobs:
            (cwd / f"{tag}.ys").write_text(script, encoding="utf-8")
            (cwd / f"{tag}.log").unlink(missing_ok=True)
        if timeout <= 0:
            for _, tag in jobs:
                yield tag, None, ""
            return
        end = time.monotonic() + timeout
        for _, tag in jobs:
            err = (cwd / f"{tag}.err").open("w+b")
            proc = subprocess.Popen(
                _yosys_argv(tag),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err,
                start_new_session=True,
            )
            running[tag] = (proc, err)
        while running:
            for tag, (proc, err) in list(running.items()):
                code = proc.poll()
                if code is None and time.monotonic() < end:
                    continue
                if code is None:  # at the limit
                    _stop(proc)
                del running[tag]
                err.seek(0)
                said = err.read().decode(errors="replace")
                err.close()
                log = cwd / f"{tag}.log"
                text = log.read_text(errors="replace") if log.exists() else ""
                yield tag, code, text + ("" if code is None else said)
            if running:
                time.sleep(POLL_S)
    finally:
        for proc, err in running.values():  # the caller has its answer: stop the rest
            _stop(proc)
            err.close()


def in_turn(run: YosysRunner) -> YosysRace:
    """A race for a runner that runs one script at a time (a test's fake Yosys): the scripts
    one after the other in the order given, each with the whole time limit; once the caller
    closes the generator, the rest do not run."""

    def race(
        cwd: Path, jobs: list[tuple[str, str]], timeout: float
    ) -> Generator[tuple[str, int | None, str], None, None]:
        for script, tag in jobs:
            code, log = run(cwd, script, tag, timeout)
            yield tag, code, log

    return race


class Clock:
    """A limit per Yosys call (`budget`) inside a limit for the whole check (`deadline`)."""

    def __init__(self, budget: float, deadline: float) -> None:
        self.budget = budget
        self.end = time.monotonic() + deadline

    def left(self) -> float:
        """Seconds the next Yosys call may take; 0 once the deadline has passed."""
        return max(0.0, min(self.budget, self.end - time.monotonic()))


def _when(cycle: object) -> str:
    return (
        "cycle 1 (power-up, with rst_n low)"
        if cycle == 1
        else f"cycle {cycle} (cycle 1 is power-up, with rst_n low)"
    )


def _points(diffs: list[dict[str, Any]]) -> str:
    return ", ".join(
        f"{d['signal']} (RTL {equiv_parse.show_bits(d['rtl'])}, "
        f"netlist {equiv_parse.show_bits(d['netlist'])})"
        for d in diffs[:8]
    )


class _Run:
    """What one check collects on the way; `verdict` turns it into the result dict."""

    def __init__(
        self,
        scratch: Path,
        run: YosysRunner,
        clock: Clock,
        t0: float,
        race: YosysRace | None = None,
    ) -> None:
        self.scratch, self.yosys, self.clock, self.t0 = scratch, run, clock, t0
        # the real runner races the real way; any other (a test's fake) runs its scripts in turn
        self.race: YosysRace = race or (race_yosys if run is run_yosys else in_turn(run))
        self.out: dict[str, Any] = {
            "seconds": {},
            "stage1": None,
            "base": None,
            "first_mismatch": None,
            "trace": [],
            "internal_inconsistency": False,
            "cycles_covered": None,
            "time_limit_hit": False,
            "detail": [],
            "netlist_sha256": None,
        }

    def timed(self, key: str, since: float) -> None:
        self.out["seconds"][key] = round(time.monotonic() - since, 2)

    def verdict(
        self, verdict: str, reason: str | None, message: str, **extra: Any
    ) -> dict[str, Any]:
        out = self.out
        out.update(extra)
        out["verdict"] = verdict
        out["ok"] = verdict == "equivalent"
        out["reason"] = reason
        out["method"] = extra.get("method")
        if verdict == "not_proven" and NOT_A_PASS not in message:
            message = f"{message} {NOT_A_PASS}"
        out["message"] = clip(message, MESSAGE_MAX)
        out["seconds"]["total"] = round(time.monotonic() - self.t0, 2)
        return _clamp(out)

    def failed(self, failure: dict[str, Any], what: str) -> dict[str, Any]:
        """A guard refusal or a Yosys failure as a verdict (never a pass)."""
        if failure["reason"] == "time_limit":
            self.out["time_limit_hit"] = True
        self.out["detail"] = failure.get("detail", [])
        message = (
            f"{what}: {failure['message']}." if what else f"{failure['message'].capitalize()}."
        )
        return self.verdict(failure["verdict"], failure["reason"], message)


def _clamp(out: dict[str, Any]) -> dict[str, Any]:
    """Clip every list and text field to the host's limits (worker/limits.py)."""

    def diffs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "signal": clip(str(d["signal"]), EQUIV_SIGNAL_MAX),
                "rtl": None if d["rtl"] is None else clip(str(d["rtl"]), EQUIV_VALUE_MAX),
                "netlist": None
                if d["netlist"] is None
                else clip(str(d["netlist"]), EQUIV_VALUE_MAX),
            }
            for d in items[:MAX_EQUIV_DIFFS]
        ]

    out["detail"] = [clip(str(x), EQUIV_DETAIL_LINE_MAX) for x in out["detail"][:MAX_EQUIV_DETAIL]]
    mismatch = out["first_mismatch"]
    if mismatch is not None:
        out["first_mismatch"] = {
            "cycle": int(mismatch["cycle"]),
            "differs": diffs(mismatch["differs"]),
        }
    base = out["base"]
    if isinstance(base, dict) and "differs" in base:
        base["differs"] = diffs(base["differs"])
    stage1 = out["stage1"]
    if isinstance(stage1, dict) and "unproven_signals" in stage1:
        stage1["unproven_signals"] = [
            clip(str(s), EQUIV_SIGNAL_MAX) for s in stage1.get("unproven_signals", [])
        ][:MAX_EQUIV_DIFFS]
    return out


def _trace(trace: list[dict[str, Any]], mismatch: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The cycles up to the mismatch (all of them if there is none), at most the last few."""
    last = int(mismatch["cycle"]) if mismatch else None
    kept = [t for t in trace if last is None or t["cycle"] <= last]
    kept = kept[-MAX_EQUIV_TRACE:]
    return [
        {
            "cycle": int(t["cycle"]),
            "inputs": {k: clip(str(v), EQUIV_VALUE_MAX) for k, v in t["inputs"].items()},
            "rtl": {k: clip(str(v), EQUIV_VALUE_MAX) for k, v in t["rtl"].items()},
            "netlist": {k: clip(str(v), EQUIV_VALUE_MAX) for k, v in t["netlist"].items()},
        }
        for t in kept
    ]


def _xz_failure(what: str) -> dict[str, Any]:
    return {
        "verdict": "error",
        "reason": "xz_constant",
        "message": what,
        "detail": [],
    }


def _netlist_xz(r: _Run) -> dict[str, Any] | None:
    """A failure if the netlist as Yosys read it holds an x or z constant, or cannot be looked at.

    The text guard (stage 0) knows the usual spellings; this one reads what Yosys made of the file,
    so a spelling nobody thought of still ends here. Never skipped: no file is a failure too."""
    try:
        text = (r.scratch / equiv_script.GATE_RTLIL).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return _xz_failure("the netlist could not be checked for unknown constants")
    bad = equiv_parse.rtlil_xz_constant(text)
    if bad is None:
        return None
    return _xz_failure(
        f"the netlist contains an unknown constant ({bad}): an input that is floating or "
        "undefined. A correct netlist has none"
    )


# -- stage 1 ----------------------------------------------------------------------------------


def _stage1(r: _Run, name: str, seq: int) -> dict[str, Any]:
    """Guards and the induction step. `status`: proven | unproven | timeout | fail."""
    for f in (
        "equiv.json",
        "equiv.il",
        equiv_script.GATE_RTLIL,
    ):  # never read a model left over from an earlier run
        (r.scratch / f).unlink(missing_ok=True)
    script = equiv_script.stage1(name, "netlist.v", seq)
    code, log = r.yosys(r.scratch, script, "equiv", r.clock.left())
    common = {
        "rtl_has_init": equiv_parse.has_inits(log),
        "rtl_has_memory": equiv_parse.has_memories(log),
    }
    if code is None and (r.scratch / "equiv.json").exists():  # the proof itself ran out of time
        bad = _netlist_xz(r)
        return (
            {"status": "fail", "failure": bad, **common} if bad else {"status": "timeout", **common}
        )
    failure = equiv_parse.guard_failure(code, log)
    if failure is not None:
        return {"status": "fail", "failure": failure, **common}
    bad = _netlist_xz(r)
    if bad is not None:
        return {"status": "fail", "failure": bad, **common}
    parsed = equiv_parse.parse_stage1(log)
    if parsed is None:
        return {
            "status": "fail",
            "failure": {
                "verdict": "error",
                "reason": "yosys_failed",
                "message": "equiv_status printed no summary",
                "detail": [],
            },
            **common,
        }
    return {"status": "proven" if parsed["unproven"] == 0 else "unproven", **parsed, **common}


# -- stage 3 ----------------------------------------------------------------------------------


def _no_model(what: str, exc: Exception) -> dict[str, Any]:
    return {
        "status": "fail",
        "failure": {
            "verdict": "error",
            "reason": "yosys_failed",
            "message": f"{what} cannot be built: {exc}",
            "detail": [],
        },
    }


def _base(r: _Run, points: list[equiv_script.Point], steps: int) -> dict[str, Any]:
    """Base case of the stage-1 induction (X run and DEF run). `status`: ok | cex | fail."""
    il = (r.scratch / "equiv.il").read_text(encoding="utf-8", errors="replace")
    try:
        model, wires = equiv_parse.base_model(il, points)
    except ValueError as exc:  # a state whose power-up value cannot be set: never a pass
        return _no_model("the base case", exc)
    (r.scratch / "base.il").write_text(model, encoding="utf-8")
    script = equiv_script.base_script(points, steps, wires)
    code, log = r.yosys(r.scratch, script, "base", r.clock.left())
    if code != 0:
        failure = equiv_parse.guard_failure(code, log) or {
            "verdict": "error",
            "reason": "yosys_failed",
            "message": "the base-case proof failed",
            "detail": [],
        }
        if failure["reason"] == "time_limit":
            failure = {**failure, "message": f"{failure['message']} ({SOLVER_HINT})"}
        return {"status": "fail", "failure": failure}
    found = equiv_parse.parse_base(log, points)
    if found["status"] == "unreadable":
        return {
            "status": "fail",
            "failure": {
                "verdict": "error",
                "reason": "yosys_failed",
                "message": f"the base case {found['run']} run printed no clear result",
                "detail": [],
            },
        }
    return found


# -- stages 2 and 4 ---------------------------------------------------------------------------


def _miter(
    r: _Run, tag: str, golds: str, gates: str, init: str, induct: str, depth: int
) -> dict[str, Any]:
    """One search on a miter. `status`: cex | proven | no_cex | fail. `init` "per_side" (a
    bounded search only): the power-up of the base case, RTL registers x and netlist flip-flops
    any defined value (`_per_side`), else every flip-flop `undef` or `zero` (one `sat` run)."""
    if init == "per_side":
        return _per_side(r, tag, golds, gates, depth)
    script = equiv_script.miter(golds, gates, tag, init, induct, depth)
    return _sat(r, tag, script, induct, depth)


def _sat(r: _Run, tag: str, script: str, induct: str, depth: int) -> dict[str, Any]:
    """Run one `sat -tempinduct` script (`induct`: a key of `equiv_script.SAT_MODE`) and read it."""
    code, log = r.yosys(r.scratch, script, tag, r.clock.left())
    return _read_sat(code, log, induct, depth)


def _read_sat(code: int | None, log: str, induct: str, depth: int) -> dict[str, Any]:
    """What a `sat -tempinduct` run says, as `_miter` returns it (exit code None: stopped at
    its time limit)."""
    if "model found for base case: FAIL" in log:
        return {"status": "cex", **equiv_parse.parse_cex(log)}
    covered = equiv_parse.covered_cycles(log)
    if induct != "base" and "Induction step proven: SUCCESS" in log:
        return {
            "status": "proven",
            "length": equiv_parse.induction_length(log),
            "covered": covered,
        }
    if code is None:
        return {"status": "no_cex", "covered": covered, "stopped": "time limit"}
    if code == 0:
        if induct != "base":
            return {"status": "no_cex", "covered": covered}
        if equiv_parse.reached_depth(log, depth):  # the search says it covered every cycle
            return {"status": "no_cex", "covered": depth}
        return {  # exit code 0 but no proof line: a cut-off or reworded log is never a pass
            "status": "fail",
            "failure": {
                "verdict": "error",
                "reason": "yosys_failed",
                "message": "the check from reset printed no result",
                "detail": [],
            },
        }
    failure = equiv_parse.guard_failure(code, log) or {
        "verdict": "error",
        "reason": "yosys_failed",
        "message": "the sat proof failed",
        "detail": [],
    }
    return {"status": "fail", "failure": failure}


def _per_side(r: _Run, tag: str, golds: str, gates: str, depth: int) -> dict[str, Any]:
    """The bounded search from reset with the per-side power-up, as `_miter` returns it.

    Write the miter (`miter.il`) and add the power-up wires of each side (`miter_ff.il`). Then
    two searches of the same problem run AT ONCE (`race_yosys`), each with the whole time one
    call may take, and with its own log and waveform file: the one-shot search
    (`equiv_script.miter_once`, tag `<tag>_once`), one `sat` problem of all `depth` cycles, and
    the step-by-step search (`equiv_script.miter_bmc`, tag `<tag>`), one more cycle per solver
    call, which finds a difference at its first cycle. Neither is always the faster one: a
    registered table takes the one-shot seconds and the step-by-step search minutes, a design
    with arithmetic the other way round. Each log is read as it ends (`equiv_parse.once_result`,
    `_read_sat`), and `_race_decision` says when the race is decided; the search still running
    then is killed."""
    for stale in ("miter.il", "miter_ff.il"):  # a file left by an earlier run is never read
        (r.scratch / stale).unlink(missing_ok=True)
    code, log = r.yosys(
        r.scratch, equiv_script.miter_model(golds, gates), f"{tag}_model", r.clock.left()
    )
    if code is None:
        return {"status": "no_cex", "covered": 0, "stopped": "time limit"}
    if code != 0:
        failure = equiv_parse.guard_failure(code, log) or {
            "verdict": "error",
            "reason": "yosys_failed",
            "message": "the miter for the check from reset could not be built",
            "detail": [],
        }
        return {"status": "fail", "failure": failure}
    try:
        il = (r.scratch / "miter.il").read_text(encoding="utf-8", errors="replace")
        model, wires = equiv_parse.miter_model(il)
    except (OSError, ValueError) as exc:
        return _no_model("the check from reset", exc)
    (r.scratch / "miter_ff.il").write_text(model, encoding="utf-8")
    once_tag = f"{tag}_once"
    jobs = [
        (equiv_script.miter_once(once_tag, wires, depth), once_tag),
        (equiv_script.miter_bmc(tag, wires, depth), tag),
    ]
    once: str | None = None
    once_log = ""
    step: dict[str, Any] | None = None
    with contextlib.closing(r.race(r.scratch, jobs, r.clock.left())) as runs:
        for ended, code, log in runs:
            if ended == once_tag:
                once, once_log = equiv_parse.once_result(code, log, wires, depth), log
            else:
                step = _read_sat(code, log, "base", depth)
            got = _race_decision(once, once_log, step, depth)
            if got is not None:
                return got  # closing the race kills the search that still runs
    # unreachable with a race that reports every script; never a pass if it does not
    return {
        "status": "fail",
        "failure": {
            "verdict": "error",
            "reason": "yosys_failed",
            "message": "the check from reset printed no result",
            "detail": [],
        },
    }


def _race_decision(
    once: str | None, once_log: str, step: dict[str, Any] | None, depth: int
) -> dict[str, Any] | None:
    """The answer of the race in `_per_side` so far, as `_miter` returns it, or None to wait for
    the search that still runs. `once`: what the one-shot search says
    (`equiv_parse.once_result`: proved, cex or unclear; None while it runs) with its log
    `once_log`; `step`: what the step-by-step search says (`_read_sat`; None while it runs).

    * The one-shot proved every cycle: proved (`covered` = `depth`).
    * The step-by-step search found a difference: that one, at its first cycle.
    * The one-shot found a difference: the step-by-step search runs on for the first differing
      cycle until it ends or reaches its limit; if it ends without a difference (it ran out of
      time, failed, or contradicts the one-shot) the one-shot's model is reported, read like
      any other counterexample (one whose differing bits cannot be read is
      `unreadable_counterexample`): never a pass.
    * The step-by-step search reached `depth` cycles without a difference: proved.
    * It stopped at its limit or failed: wait for the one-shot, which may still prove every
      cycle or find a difference; if it is unclear, the step-by-step result stands (a time
      limit, an error).
    * The one-shot is unclear (a time-out, an error, a process that died, a log that does not
      show the problem asked for): the step-by-step search decides."""
    if once == "proved":
        return {"status": "no_cex", "covered": depth}
    if step is None:
        return None
    if step["status"] == "cex":
        return step
    if once == "cex":
        return {"status": "cex", **equiv_parse.parse_cex(once_log)}
    if step["status"] == "no_cex" and not step.get("stopped"):
        return step
    if once is None:
        return None
    return step


def _mismatch(got: dict[str, Any]) -> dict[str, Any] | None:
    """The first mismatch of a counterexample, or None when its differing bits cannot be read."""
    fm = got.get("first_mismatch")
    return fm if fm and fm.get("differs") else None


def _unreadable_cex(r: _Run, got: dict[str, Any], where: str) -> dict[str, Any]:
    """Yosys found a difference but its table gives no differing bit: not proven, never a pass."""
    r.out["trace"] = _trace(got.get("trace", []), None)
    return r.verdict(
        "not_proven",
        "unreadable_counterexample",
        f"Could not prove equivalence: the solver found a difference {where} but the differing "
        "bits could not be read from its output.",
    )


def _hdl_function(label: str, work: Path = Path("/work")) -> str:
    """`, hdl/x.py, function f` for the MyHDL block `label`, or "" if it cannot be found."""
    if not label:
        return ""
    function = myhdl_map.find_function(label, myhdl_map.read_functions(work))
    return f", {function.file}, function {function.name}" if function else ""


COMB_LINT_TIMEOUT_S = float(emit.TOOL_TIMEOUT_S)  # the same as the emit lint
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LintRun:
    """What a Verilator run left: the exit code, stdout and stderr together, and whether it was
    stopped for taking too long."""

    code: int
    output: str
    timed_out: bool = False


def _run_verilator(argv: list[str], cwd: Path) -> LintRun | None:
    """Run Verilator; None only if it could not be STARTED at all (no binary)."""
    return _run_tool(argv, cwd)


def _run_icarus(argv: list[str], cwd: Path) -> LintRun | None:
    """Run Icarus Verilog (`iverilog`); None only if it could not be STARTED at all."""
    return _run_tool(argv, cwd)


def _run_tool(argv: list[str], cwd: Path) -> LintRun | None:
    """Run a tool with the emit lint's time limit; None only if it could not be STARTED."""
    try:
        proc = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=COMB_LINT_TIMEOUT_S, check=False
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stderr or exc.stdout or ""
        return LintRun(-1, partial if isinstance(partial, str) else partial.decode("latin-1"), True)
    except OSError:
        return None
    return LintRun(proc.returncode, proc.stderr + "\n" + proc.stdout)


def _comb_order(scratch: Path, sources: list[tuple[str, str]], name: str) -> tuple[str, str] | None:
    """`(reason, sentence)` if the `always @*` blocks cannot be shown to write before they read.

    A simulator does not rerun such a block for its own write, so a signal read before it is
    written holds the value of the run before (`y = t; t = cnt;` gives y the old t); the gates
    give the new one. Verilator finds it for an `always_comb` (ALWCOMBORDER), so the RTL is linted
    once more with every `always @*` and `always @(*)` written as `always_comb`, on a copy in
    `scratch`, with the flags of the emit lint (and `-Wno-fatal`, so warnings do not stop it).
    The pass must not pass by giving up:

    * ALWCOMBORDER in one of our files: `guard_sim_synth_differ`;
    * the run takes longer than the emit lint may: `time_limit`;
    * the copy does not lint (an error, or a failing exit code) while the RTL does:
      `guard_sim_synth_differ`, naming the first error, because the check cannot tell whether
      the copy hides such a block;
    * Verilator cannot be started at all (no binary): inconclusive, logged, nothing is refused.

    Nothing runs if there is no `always @*`."""
    copies = {fname: equiv_sv.always_comb_copy(text) for fname, text in sources}
    if all(copy is None for copy in copies.values()):
        return None
    folder = scratch / "comb_order"
    folder.mkdir(parents=True, exist_ok=True)
    for fname, text in sources:
        copy = copies[fname] if copies[fname] is not None else equiv_sv.clean(text)
        (folder / fname).write_bytes(str(copy).encode("latin-1"))
    command = [
        "verilator", "--lint-only", "-Wall", "-Wno-fatal", "--timing", "--timescale", "1ns/1ps",
        "-Wno-DECLFILENAME", "--top-module", f"tt_um_{name}", "project.v", f"{name}.v",
    ]  # fmt: skip
    run = _run_verilator(command, folder)
    if run is None:
        log.warning("verilator could not be started: the always_comb order check was skipped")
        return None
    found = lint.parse(run.output)
    for item in found:
        if item["code"] == "ALWCOMBORDER" and item["file"] in copies and item["line"]:
            signal = re.search(r"'([^']+)'", item["msg"])
            shown = f" (`{signal.group(1)}`)" if signal else ""
            return (
                "guard_sim_synth_differ",
                f"the RTL ({item['file']}, line {item['line']}) has a combinational block that "
                f"reads a signal before it writes it{shown}: a simulator does not rerun the block "
                "for its own write, the gates do. Write each signal before you read it.",
            )
    if run.timed_out:
        return (
            "time_limit",
            f"the lint that looks for a combinational block reading a signal before it writes "
            f"it did not finish within {COMB_LINT_TIMEOUT_S:.0f} s.",
        )
    errors = [item for item in found if item["severity"] == "error"]
    if errors or run.code != 0:
        if errors:
            first = errors[0]
            where = f"{first['file']}, line {first['line']}: " if first["line"] else ""
            why = where + str(first["msg"])
        else:
            why = clip(run.output.strip().splitlines()[-1] if run.output.strip() else "failed", 200)
        return (
            "guard_sim_synth_differ",
            "the RTL does not lint with every `always @*` written as `always_comb` (the check "
            f"that looks for a combinational block reading a signal before it writes it): {why}. "
            "The check cannot tell whether such a block is in the RTL: write the block so it "
            "lints as `always_comb`.",
        )
    return None


# The race and x warnings of the emit lint that only a comment can switch off there. Not
# CASEINCOMPLETE: a flag switches it off for Amaranth cores, and an incomplete case only makes a
# latch (which the proof refuses) or a register that keeps its value.
HIDDEN_LINT_CODES = equiv_sv.LINT_CODES_NOT_TO_HIDE - {"CASEINCOMPLETE"}


def _hidden_lint(
    scratch: Path, sources: list[tuple[str, str]], name: str
) -> tuple[str, str] | None:
    """`(reason, sentence)` if the RTL, linted once more with every comment and attribute
    removed (`equiv_sv.clean`, the text the proof reads), shows a race or x warning
    (`HIDDEN_LINT_CODES`: BLKSEQ, COMBDLY, MULTIDRIVEN, LATCH, CASEX, ...) in one of our files.

    The emit lint makes every warning fatal, so such a warning was switched off by a comment:
    a `lint_off` in any spelling Verilator reads (round 21: `/* verilator lint_off
    WIDTH//,BLKSEQ */`, whose `//` Verilator deletes, and `// verilator lint_off WIDTH /*`,
    which opens a block comment that hides the code after it), or a `synopsys` pragma. This
    makes the check independent of how Verilator parses its comments; `equiv_sv` still names
    the common spellings first, at their line. Same flags as the always_comb pass, with
    `-Wno-fatal`, on a copy in `scratch`. A time-out is `time_limit`; a copy that does not lint
    (an error, a failing exit code) while the RTL does is refused; Verilator that cannot be
    started at all is inconclusive (logged), as for the other Verilator passes."""
    folder = scratch / "hidden_lint"
    folder.mkdir(parents=True, exist_ok=True)
    for fname, text in sources:
        (folder / fname).write_bytes(equiv_sv.clean(text).encode("latin-1"))
    command = [
        "verilator", "--lint-only", "-Wall", "-Wno-fatal", "--timing", "--timescale", "1ns/1ps",
        "-Wno-DECLFILENAME", "--top-module", f"tt_um_{name}", "project.v", f"{name}.v",
    ]  # fmt: skip
    run = _run_verilator(command, folder)
    if run is None:
        log.warning("verilator could not be started: the lint without comments was skipped")
        return None
    ours = {fname for fname, _ in sources}
    found = lint.parse(run.output)
    for item in found:
        if item["code"] in HIDDEN_LINT_CODES and item["file"] in ours:
            where = f"{item['file']}, line {item['line']}" if item["line"] else item["file"]
            return (
                "guard_sim_synth_differ",
                f"the RTL ({where}) has a Verilator {item['code']} warning that a comment "
                f"switched off in the emit lint ({clip(str(item['msg']), 160)}). Such a warning "
                "marks a race or an x that a simulator and the gates can treat differently, so "
                "the check cannot vouch for the design. Remove the comment that hides it (a "
                "`lint_off`, also inside a list or another comment, or a `synopsys` pragma) and "
                "fix what the warning says.",
            )
    if run.timed_out:
        return (
            "time_limit",
            f"the lint of the RTL without its comments did not finish within "
            f"{COMB_LINT_TIMEOUT_S:.0f} s.",
        )
    errors = [item for item in found if item["severity"] == "error"]
    if errors or run.code != 0:
        if errors:
            first = errors[0]
            where = f"{first['file']}, line {first['line']}: " if first["line"] else ""
            why = where + str(first["msg"])
        else:
            why = clip(run.output.strip().splitlines()[-1] if run.output.strip() else "failed", 200)
        return (
            "guard_sim_synth_differ",
            f"the RTL does not lint once its comments are removed (the check that looks for a "
            f"warning a comment switched off): {why}. The check cannot tell whether a comment "
            "hides a race: write the RTL so it lints without its comments.",
        )
    return None


def _tree_checks(r: _Run, sources: list[tuple[str, str]], name: str) -> dict[str, Any] | None:
    """A `not_proven` (or `error`) verdict if Verilator's reading of the RTL cannot be trusted,
    shows an impure function or a combinational block that can read a signal before it writes
    it, else None.

    A simulator's `always @*` does not re-run when a signal read only inside a called function
    changes; synthesis inlines the function. Scope cannot be judged from tokens, so Verilator
    resolves the names (`--json-only` writes its syntax tree, `equiv_tree` reads it) on the
    cleaned RTL that Yosys reads, with the top module and flags of the always_comb pass. It runs
    for every design (a parameter with an x bit needs no block or function to matter: a generate
    `case` of continuous assignments is enough). In this order, and like the always_comb pass it
    must not pass by giving up:

    1. Verilator: the run takes longer than the emit lint may: `time_limit`; a failing exit code,
       no readable tree, or a node the walker does not understand: `guard_sim_synth_differ`,
       naming the first error. Verilator cannot be started at all: inconclusive, logged, nothing
       is refused (it cannot happen in practice: the worker runs inside the toolchain image,
       which holds Verilator, and the emit lint that ran before this check would have failed).
    2. `equiv_tree.xz_parameter`: no parameter, localparam or genvar-derived constant may have an
       x or z bit (`1/0`): the tools can elaborate it differently. `equiv_tree.x_condition`, on
       the tree Verilator dumps before it evaluates the parameters (`--dump-tree-json`, the
       `linkinc` stage): no generate condition may hold an operator that can give x (`1/Z`):
       Verilator takes the first branch for an x `if` condition and drops it from the tree.
    3. `_icarus_checks`: the simulator's own elaboration (Icarus): it must compile the RTL, and
       no parameter may have an x or z bit there, nor a constant select be out of range
       (Verilator folds those to a value, Icarus and Yosys read x), nor a parameter have other
       values than in Verilator's tree in some instance or loop pass
       (`equiv_tree.parameter_values`; a select outside the range inside a constant function
       leaves no x and no warning), nor the same values with another width or sign
       (`equiv_tree.parameter_shapes`; round 23: Icarus makes an untyped `8'hff + 8'h00` 9 bits
       wide, Verilator and the gates 8, and `{M, M}` differs; a width of exactly 32 bits in
       Verilator that Icarus makes wider passes), nor a parameter whose value holds a call that
       can give x go uncompared (`equiv_tree.valued_calls`: a real, a string).
    4. Yosys elaborates the same cleaned RTL to RTLIL (only if Verilator gave a tree). A failure
       or a time out is the verdict of `equiv_parse.guard_failure`, never a pass.
    5. `equiv_elab.unmatched`: every block and call Yosys keeps must be in Verilator's tree
       (they can elaborate a constant that is x, such as `1/0`, differently; Verilator drops a
       call that a constant makes dead), and no function is called by a hierarchical name.
    6. `equiv_tree.impure_function`: a function may read only its arguments.
    7. `equiv_tree.early_read`: a combinational block may not read a signal before it writes it
       on any path through the block (the ALWCOMBORDER pass, `_comb_order`, runs after this as
       a second line).
    8. `equiv_tree.constant_call`, on the tree before V3Param: no call of a design function
       that can give x for constant arguments (a select at a computed index, a division) with
       constant arguments outside a parameter value (a simulator works it out itself, and an
       `if` in the function can turn an x into another value than Yosys uses; only parameter
       values are compared with the simulator's: compute it in a localparam first). A select
       whose index is provably inside its range for every value of the operands, and a divisor
       provably nonzero, are no x source (round 16, `equiv_tree._Ranges`); a parameter counts
       there with the one value that both tools give it in every instance
       (`equiv_tree.agreed_values` of Verilator's and Icarus's values), so this runs after
       `_icarus_checks`. Such a call still counts when an argument can be x, a real among them
       (an infinite or NaN real is an all-x integer to a simulator), a hierarchical name and a
       parameter whose value is not a plain number in both tools (`equiv_tree.plain_values`),
       unless neither tool builds that parameter (`equiv_tree.built_values`: its code is dead
       in every tool). A real or a hierarchical name in an argument refuses every call of a
       design function, wherever it is (round 20: also in a parameter value or an override,
       and in an argument that reads a variable: Yosys converts the real silently wherever it
       works a call out); a function's own localparam that only Icarus lists, with a plain
       number, holds no real (round 21, `equiv_tree.simulator_only_values`). Then
       `_yosys_real_x`: no real that is infinite or NaN converted to an integer anywhere Yosys
       converts one (its warning: an assignment, also inside a function, a wire, a port). Then
       `_yosys_outside`: no select outside the range in Yosys's elaboration (its warning; Yosys
       sees one inside a function it inlines, also in an arm of `?:` that a constant condition
       never selects). Then `equiv_tree.narrow_argument`, on the tree before V3Param (round
       25): no call of a design function with an argument narrower than the input, and no size
       or type cast of a narrower value, where a part that reads no signal can carry, borrow or
       flip high bits (`+ - * << ** ~`, unary minus) unless its value is proven the same at every
       width: Yosys 0.62 works such a part out at the argument's own width, a simulator at the
       input's (`clog2(LAST + 1'b1)` with a 4-bit `LAST = 15`: 16 in a simulator, 0 in the
       gates); after the real and select findings, which name a fail-closed width more exactly
       (`f(R + a)` with an infinite real `R`). Then `equiv_tree.uncompared_local`: a localparam
       declared inside a function whose value is not compared with the simulator's (round 23:
       Verilator leaves out a function it only works out as a constant, so `localparam M = 1 <<
       32;` there gave `wire [h(3):0] w` another width in Icarus than in the gates) must be one
       number as written. Last, so that a more specific finding about the same code is named
       first.
    """
    cleaned = {fname: equiv_sv.clean(text) for fname, text in sources}
    folder = r.scratch / "func_tree"
    folder.mkdir(parents=True, exist_ok=True)
    for fname, text in cleaned.items():
        (folder / fname).write_bytes(text.encode("latin-1"))
    top = f"tt_um_{name}"
    # -fno-dfg: Verilator's dataflow pass would fold a one-assignment `always` into the code that
    # reads it, and the block would have no node at its keyword for the position cross-check
    # (nor statements for the read-before-write walk)
    # --dump-tree-json: the tree of every stage as well, for the one before V3Param (`linkinc`)
    command = [
        "verilator", "--json-only", "--dump-tree-json", "-fno-dfg", "-Wno-fatal", "--timing",
        "--timescale", "1ns/1ps", "-Wno-DECLFILENAME", "--top-module", top, "project.v",
        f"{name}.v",
    ]  # fmt: skip
    t0 = time.monotonic()
    run = _run_verilator(command, folder)
    r.timed("tree", t0)
    if run is None:
        log.warning(
            "verilator could not be started: the Verilator-based checks "
            "(function purity, elaboration cross-check) were skipped"
        )
        return None

    def refuse(reason: str, sentence: str) -> dict[str, Any]:
        return r.verdict("not_proven", reason, f"Could not prove equivalence: {sentence}")

    def unreadable(why: str, what: str = "the RTL with Verilator") -> dict[str, Any]:
        return refuse(
            "guard_sim_synth_differ", f"the check could not read {what}: {clip(why, 200)}"
        )

    if run.timed_out:
        return refuse(
            "time_limit",
            "the check of the functions in the RTL (Verilator) did not finish within "
            f"{COMB_LINT_TIMEOUT_S:.0f} s.",
        )
    if run.code != 0:
        lines = [x for x in run.output.splitlines() if x.strip()]
        first = next((x for x in lines if x.startswith("%Error")), lines[-1] if lines else "failed")
        return unreadable(first.strip())
    try:
        tree = json.loads((folder / "obj_dir" / f"V{top}.tree.json").read_text("latin-1"))
        meta = json.loads((folder / "obj_dir" / f"V{top}.tree.meta.json").read_text("latin-1"))
        stages = sorted((folder / "obj_dir").glob(f"V{top}_*_linkinc.tree.json"))
        if len(stages) != 1:
            raise equiv_tree.TreeError("no tree of the stage before the parameters are evaluated")
        before = json.loads(stages[0].read_text("latin-1"))
        unknown = equiv_tree.xz_parameter(tree, meta, list(cleaned))
        condition = equiv_tree.x_condition(before, meta, list(cleaned))
        valued = equiv_tree.valued_calls(before, meta, list(cleaned))  # judged with Icarus's
        values = equiv_tree.parameter_values(tree, meta, list(cleaned))  # judged with Icarus's
        shapes = equiv_tree.parameter_shapes(tree, meta, list(cleaned))  # judged with Icarus's
        found = equiv_tree.impure_function(tree, meta, list(cleaned))  # judged after Yosys
        early = equiv_tree.early_read(tree, meta, list(cleaned))  # judged after purity
    except (OSError, ValueError, equiv_tree.TreeError, RecursionError) as exc:
        return unreadable(f"no usable syntax tree ({type(exc).__name__}: {exc})")
    # the root causes of a different elaboration: named first
    for root in (unknown, condition):
        if root is not None:
            return refuse("guard_sim_synth_differ", f"{root.sentence}.")
    simulator, simulated = _icarus_checks(r, cleaned, name, values, shapes, valued)
    r.timed("tree", t0)  # the Verilator and the Icarus run
    if simulator is not None:
        return simulator
    try:  # judged last; a parameter counts with the one value both tools give it
        agreed = equiv_tree.agreed_values(values, simulated)
        plain = equiv_tree.plain_values(values, simulated)
        built = equiv_tree.built_values(values, simulated)
        lone = equiv_tree.simulator_only_values(values, simulated)
        constant = equiv_tree.constant_call(before, meta, list(cleaned), agreed, plain, built, lone)
        narrow = equiv_tree.narrow_argument(before, meta, list(cleaned), agreed, shapes)
        local = equiv_tree.uncompared_local(before, meta, list(cleaned), values, simulated)
    except (ValueError, equiv_tree.TreeError, RecursionError) as exc:
        return unreadable(f"no usable syntax tree ({type(exc).__name__}: {exc})")

    # Yosys's elaboration of the same text, to see which blocks it keeps
    elab = r.scratch / "elab"
    elab.mkdir(parents=True, exist_ok=True)
    (elab / "elab.il").unlink(missing_ok=True)
    for fname, text in cleaned.items():
        (elab / fname).write_bytes(text.encode("latin-1"))
    t0 = time.monotonic()
    code, text = r.yosys(elab, equiv_script.elaboration(name), "elab", r.clock.left())
    r.timed("elab", t0)
    failure = equiv_parse.guard_failure(code, text)
    if failure is not None:
        return r.failed(failure, "Could not read the RTL's elaboration")
    outside = _yosys_outside(text)  # judged last
    real = _yosys_real_x(text)
    what = "the elaboration of the RTL"
    try:
        rtlil = (elab / "elab.il").read_text("latin-1")
        differ = equiv_elab.unmatched(tree, meta, rtlil, cleaned)
    except (OSError, ValueError, equiv_elab.ElabError, equiv_tree.TreeError, RecursionError) as exc:
        return unreadable(f"{type(exc).__name__}: {exc}", what)
    if differ is not None:
        return refuse("guard_sim_synth_differ", differ)
    if found is not None:
        return refuse("guard_sim_synth_differ", f"{found.sentence}.")
    if early is not None:
        return refuse("guard_sim_synth_differ", f"{early.sentence}.")
    if constant is not None:
        return refuse("guard_sim_synth_differ", f"{constant.sentence}.")
    if real is not None:
        return refuse("guard_sim_synth_differ", real)
    if outside is not None:
        return refuse("guard_sim_synth_differ", outside)
    if narrow is not None:
        return refuse("guard_sim_synth_differ", f"{narrow.sentence}.")
    if local is not None:
        return refuse("guard_sim_synth_differ", f"{local.sentence}.")
    return None


# Yosys's warning when it turns a real into bits; an infinite or NaN real comes out all x
# (`converting real value inf to binary 32'x.`; a finite one gives a number: `... to binary 3.`)
_REAL_TO_X = re.compile(
    r"^(?:([^:\s]+):(\d+): )?Warning: converting real value (\S+) to binary \d+'x", re.M
)


def _yosys_real_x(log_text: str) -> str | None:
    """The sentence for a real that is infinite or not a number (NaN) and that Yosys converted
    to an integer when it elaborated the RTL (its warning "converting real value inf to binary
    32'x"), else None. A simulator converts such a real to an all-x integer too, and an `if`, a
    `!` or a case label turns that x into a 0 or 1 that the gates need not give: the RTL's x is a
    don't-care to the proof. Yosys warns for such a conversion in an assignment or a
    connection, wherever it is: an assignment in an always block or a function (also one it
    works out with constant arguments), a wire's value, an assign, a port connection, a
    parameter declared with a type or range (round 19: `v = R;` in a function,
    `w <= R;`, `wire [3:0] w = 4'(integer'(R));` as a case label, `.a(R)`, all with
    `R = 1.0e999`). It does not warn for a real that it passes as an argument to a function it
    works out itself (`realAsConst` in `eval_const_function` prints nothing): a call with
    constant arguments (`f(R)`, `f(g.R)`), a call in a parameter value or an override
    (`localparam [7:0] K = g(R)`), a call whose argument reads a loop variable once Yosys
    unrolls the loop (`f(R + i)`) and a call inside a function it works out (`h = f(R + a)`):
    `equiv_tree.constant_call` refuses a real argument of every call (round 20)."""
    found = _REAL_TO_X.search(log_text)
    if found is None:
        return None
    file, line, value = found.groups()
    where = f" ({file}, line {line})" if file else ""
    return (
        f"the RTL{where} converts a real that is infinite or not a number (Yosys: `{value}`) to "
        "an integer. A simulator makes that integer all x, and an `if`, a `!` or a case label "
        "can turn the x into a 0 or 1 that the gates need not give, so the check cannot vouch "
        "for the design. Use a real only to compute an integer parameter or localparam, and "
        "keep its value finite (`1.0e999` and `$sqrt(-1.0)` are not)."
    )


_OUT_OF_BOUNDS = re.compile(r"^(?:([^:\s]+):(\d+): )?Warning: (.*out of bounds.*)$", re.M)


def _yosys_outside(log_text: str) -> str | None:
    """The sentence for a select outside the range that Yosys found when it elaborated the RTL
    (its warning "Range select out of bounds on signal ...: Setting result bit to undef"), else
    None. Yosys sees such a select once it has inlined a function or unrolled a loop: `v[k]` of
    a 4-bit argument `v` with a constant `k = 6` (round 15: `pick(ui_in[3:0], 6)`), or `a[i+1]`
    in the last pass of a loop, also in an arm of `?:` that the loop index never selects (round
    16 ffuntern: `(i == 7) ? 1'b0 : getbit(ui_in, i + 1)`; with an `if (i < 7)` around the call
    Yosys drops that pass's select). It is x to a simulator, which an `if` or `!` can turn into
    a 0 or 1 that the gates need not give; the RTL's x is a don't-care to the proof. Icarus
    warns only about a select whose index is constant in the text (`equiv_icarus.x_select`)."""
    found = _OUT_OF_BOUNDS.search(log_text)
    if found is None:
        return None
    file, line, said = found.groups()
    where = f" ({file}, line {line})" if file else ""
    return (
        f"the RTL{where} selects outside the range of a vector once Yosys works the index out "
        f'(in a function it inlines or a loop it unrolls: "{clip(said, 160)}"). That bit is x '
        "to a simulator, and an `if` or `!` can turn it into a 0 or 1 that the gates need not "
        "give, so the check cannot vouch for the design. Keep every select inside the declared "
        "range. A select in an arm of `?:` that a constant condition never selects is refused "
        "too, since Yosys still works its index out (`(i == 7) ? 1'b0 : v[i + 1]` in a loop): "
        "guard it with an `if` instead (`if (i < 7)`), and Yosys drops it."
    )


# What to write instead when Icarus 12 stops on one of its own assertions: (a piece of the
# assertion line, the advice). Both assertions come from a parameter or localparam with more than
# one packed dimension (round 14: `localparam pair_t P = 8'h5a;` of a `typedef logic [1:0][3:0]
# pair_t`, alone or through `P[1]`, `$left(P, 2)`, `$size(P, 2)`); a wire or reg with two packed
# dimensions compiles.
_ICARUS_ASSERTIONS = (
    (("packed_dims.size() == 1", "dim == 1"),
     "Icarus 12 stops like this on a parameter or localparam with more than one packed dimension "
     "(`localparam pair_t P` of a `typedef logic [1:0][3:0] pair_t`, also through `$size(P, 2)` "
     "or `$left(P, 2)`): declare it as a plain vector (`localparam [7:0] P`), take the pieces "
     "with a part-select (`P[7:4]`, `P[4*i +: 4]`) and write a width as a number or a "
     "localparam."),
)  # fmt: skip


def _icarus_crash(run: LintRun, lines: list[str]) -> str | None:
    """The sentence for an Icarus run that stopped on an internal error (an assertion of its own,
    an internal error, or killed by a signal such as the abort after an assertion), else None.
    It quotes the assertion line, says that this is a limit of the simulator and, for a known
    assertion, what to write instead. Still a refusal: the check cannot see what a simulator
    makes of the RTL."""
    said = next((x for x in lines if ": assert:" in x or "internal error" in x.lower()), None)
    if said is None and not (run.code < 0 or run.code >= 128):
        return None
    shown = clip(said or (lines[-1] if lines else f"exit code {run.code}"), 200)
    advice = next(
        (text for keys, text in _ICARUS_ASSERTIONS if said and any(k in said for k in keys)),
        "Write the construct on that line in a simpler form.",
    )
    return (
        f"the simulator (Icarus 12, iverilog -g2012) stopped with an internal error on the RTL "
        f"(`{shown}`). That is a limit of the simulator, not an error in your design, but the "
        f"check cannot see what a simulator makes of the RTL, so it cannot vouch for it. {advice}"
    )


def _icarus_checks(
    r: _Run,
    cleaned: dict[str, str],
    name: str,
    values: equiv_tree.ParamValues,
    shapes: equiv_tree.ParamShapes,
    valued: frozenset[equiv_tree.ParamKey],
) -> tuple[dict[str, Any] | None, equiv_tree.ParamValues]:
    """A `not_proven` verdict if the simulator (Icarus) elaborates a constant of the RTL to a
    value with an x or z bit, or a parameter to other values than Verilator did (`values`, from
    its tree, instance by instance) or to the same values with another width or sign
    (`shapes`), or if a parameter whose value holds a call that can give x (`valued`,
    `equiv_tree.valued_calls`) is not compared, else None (`equiv_icarus`). `cleaned` is the
    cleaned RTL that Yosys reads. Like the Verilator run it must not pass by giving up:

    * the run takes longer than the emit lint may: `time_limit`;
    * Icarus does not compile the RTL (a failing exit code or no output): `guard_sim_synth_differ`,
      naming the first error: the check cannot see what a simulator makes of it; when Icarus
      stopped on an internal error of its own (an assertion, then an abort), the message quotes
      the assertion line, says that it is a limit of the simulator and what to write instead
      (`_icarus_crash`);
    * output it cannot read: `guard_sim_synth_differ`;
    * a parameter, localparam or genvar with an x or z bit (`equiv_icarus.x_parameter`), then a
      constant select outside the range (`equiv_icarus.x_select`), then a parameter or
      localparam whose values differ from Verilator's in some instance or loop pass
      (`equiv_icarus.disagreement`), then one whose values agree but whose width or sign does
      not (`equiv_icarus.shape_disagreement`: an untyped `8'hff + 8'h00` is 9 bits wide in
      Icarus, 8 in Verilator), then one of `valued` that is not compared (a real, a string, a
      parameter only one tool lists: `equiv_icarus.uncompared`): `guard_sim_synth_differ`;
    * Icarus cannot be started at all: inconclusive, logged, nothing is refused (it cannot happen
      in practice: the worker runs inside the toolchain image, which holds Icarus).

    Also returns the simulator's parameter values (`equiv_icarus.parameter_values`; empty when
    it gave none), for `equiv_tree.agreed_values`."""
    folder = r.scratch / "sim_elab"
    folder.mkdir(parents=True, exist_ok=True)
    for fname, text in cleaned.items():
        (folder / fname).write_bytes(text.encode("latin-1"))
    (folder / "sim.vvp").unlink(missing_ok=True)
    command = [
        "iverilog", "-g2012", "-Wselect-range", "-o", "sim.vvp", "-s", f"tt_um_{name}",
        "project.v", f"{name}.v",
    ]  # fmt: skip
    run = _run_icarus(command, folder)
    if run is None:
        log.warning("iverilog could not be started: the simulator's constants were not checked")
        return None, {}

    def refuse(reason: str, sentence: str) -> tuple[dict[str, Any], equiv_tree.ParamValues]:
        return r.verdict("not_proven", reason, f"Could not prove equivalence: {sentence}"), {}

    if run.timed_out:
        return refuse(
            "time_limit",
            "the simulator (Icarus) did not compile the RTL within "
            f"{COMB_LINT_TIMEOUT_S:.0f} s, so the check cannot see its constants.",
        )
    ours = list(cleaned)
    try:
        vvp = (folder / "sim.vvp").read_text("latin-1") if run.code == 0 else None
    except OSError:
        vvp = None
    if vvp is None:
        lines = [x.strip() for x in run.output.splitlines() if x.strip()]
        crash = _icarus_crash(run, lines)
        if crash is not None:
            return refuse("guard_sim_synth_differ", crash)
        first = next((x for x in lines if ": error:" in x or ": sorry:" in x), None)
        first = first or next((x for x in lines if "error" in x.lower()), None)
        why = clip(first or (lines[-1] if lines else "it failed"), 200)
        return refuse(
            "guard_sim_synth_differ",
            f"the simulator (Icarus, iverilog -g2012) cannot compile the RTL ({why}), so the "
            "check cannot see what a simulator makes of it. Write the RTL so that Icarus "
            "compiles it.",
        )
    try:
        simulated = equiv_icarus.parameter_values(vvp, ours)
        found = (
            equiv_icarus.x_parameter(vvp, ours)
            or equiv_icarus.x_select(run.output, ours)
            or equiv_icarus.disagreement(simulated, values, ours)
            or equiv_icarus.shape_disagreement(
                equiv_icarus.parameter_shapes(vvp, ours), shapes, ours
            )
            or equiv_icarus.uncompared(simulated, values, valued, ours)
        )
    except equiv_icarus.IcarusError as exc:
        return refuse(
            "guard_sim_synth_differ",
            f"the check could not read the simulator's (Icarus) elaboration of the RTL: {exc}.",
        )
    if found is not None:
        return refuse("guard_sim_synth_differ", f"{found.sentence}.")
    return None, simulated


def check(
    rtl_dir: Path,
    name: str,
    netlist: Path,
    scratch: Path,
    *,
    seq: int = SEQ,
    depth: int = DEPTH,
    budget_s: float = BUDGET_S,
    deadline_s: float = DEADLINE_S,
    yosys: YosysRunner = run_yosys,
    strict_core: bool = True,
    race: YosysRace | None = None,
) -> dict[str, Any]:
    """Check the RTL in `rtl_dir` (`project.v`, `<name>.v`) against the netlist FILE `netlist`.

    `yosys` runs one Yosys script, `race` several at once (stage 4); by default `race_yosys`
    for the real runner, and the scripts in turn through `yosys` for any other (`in_turn`).

    `strict_core`: judge `<name>.v` by the two rules that only make sense for a core that does not
    list level signals itself (every level-sensitive `always @(a or b)` and every function or
    task without `automatic` is refused). True for Verilog (hand-written) and Amaranth (its
    combinational logic is `@*`); False for MyHDL, whose `@always_comb` is written as
    `always @(a, b)`: its core is judged by the level-list rule instead (a level-sensitive block
    must list every signal it reads; `equiv_sv.refusal`, `myhdl_core`).

    The inputs are copied into `scratch` first; every Yosys call runs there. Returns the verdict
    dict described at the top of this module. It never raises: an unexpected failure inside the
    check is an `error` verdict (never a pass).
    """
    t0 = time.monotonic()
    try:
        return _check(
            rtl_dir,
            name,
            netlist,
            scratch,
            seq,
            depth,
            budget_s,
            deadline_s,
            yosys,
            t0,
            strict_core,
            race,
        )
    except Exception as exc:
        failed = _Run(scratch, yosys, Clock(budget_s, deadline_s), t0, race)
        return failed.verdict(
            "error",
            "internal_error",
            f"The equivalence check failed inside ({type(exc).__name__}: {exc}).",
        )


def _check(
    rtl_dir: Path,
    name: str,
    netlist: Path,
    scratch: Path,
    seq: int,
    depth: int,
    budget_s: float,
    deadline_s: float,
    yosys: YosysRunner,
    t0: float,
    strict_core: bool,
    race: YosysRace | None,
) -> dict[str, Any]:
    scratch.mkdir(parents=True, exist_ok=True)
    r = _Run(scratch, yosys, Clock(budget_s, deadline_s), t0, race)
    try:
        for fname in ("project.v", f"{name}.v"):
            shutil.copyfile(rtl_dir / fname, scratch / fname)
        if netlist.stat().st_size > NETLIST_MAX_BYTES:
            return r.verdict("error", "bad_input", "The netlist is too large to check.")
        shutil.copyfile(netlist, scratch / "netlist.v")
        data = (scratch / "netlist.v").read_bytes()
        r.out["netlist_sha256"] = hashlib.sha256(data).hexdigest()
        text = data.decode("latin-1")
    except OSError as exc:
        return r.verdict("error", "bad_input", f"The files to check could not be read ({exc}).")

    # stage 0: the netlist must not contain x/z constants (our synthesis ties undefined inputs low)
    if (bad := equiv_parse.xz_constant(text)) is not None:
        return r.verdict(
            "error",
            "xz_constant",
            f"The netlist contains an unknown constant ({bad}): an input that is floating or "
            "undefined. A correct netlist has none.",
        )

    # the netlist file is a flat list of library cells: no include, no module of its own
    if (odd := equiv_parse.netlist_text_problem(text, f"tt_um_{name}")) is not None:
        return r.verdict(
            "error",
            "netlist_modules",
            f"The netlist file has {odd}: it must hold the design's one module and library "
            "cells only.",
        )

    # What the proof compares with: the RTL as Yosys reads it, with comments and attributes
    # blanked out and no macros left (hot comments, `(* nosync *)` would change what Yosys
    # builds). Constructs where a simulator and Yosys read the RTL differently, and that the
    # check can spot, are refused; the rest is left to gate-level simulation.
    # Bytes, not text: a file is read as latin-1 (one character per byte, nothing replaced, no
    # newline translation) and written back the same way, so the proof reads the bytes that the
    # synthesis read, minus the comments and attributes.
    try:
        sources = [
            (fname, (scratch / fname).read_bytes().decode("latin-1"))
            for fname in ("project.v", f"{name}.v")
        ]
        core = {f"{name}.v"}
        found = equiv_sv.refusal(
            sources, core if strict_core else set(), set() if strict_core else core
        )
        if found is not None:
            return r.verdict(
                "not_proven",
                "guard_include" if found.code == "include" else "guard_sim_synth_differ",
                f"Could not prove equivalence: "
                f"{equiv_sv.plain_message(found, _hdl_function(found.label))}",
            )
        refused = _tree_checks(r, sources, name)
        if refused is not None:
            return refused
        comb = _comb_order(scratch, sources, name)
        if comb is not None:
            return r.verdict("not_proven", comb[0], f"Could not prove equivalence: {comb[1]}")
        hidden = _hidden_lint(scratch, sources, name)
        if hidden is not None:
            return r.verdict("not_proven", hidden[0], f"Could not prove equivalence: {hidden[1]}")
        for fname, text in sources:  # the proof reads these cleaned copies
            (scratch / fname).write_bytes(equiv_sv.clean(text).encode("latin-1"))
    except OSError as exc:
        return r.verdict("error", "bad_input", f"The files to check could not be read ({exc}).")

    # stage 1: guards and the induction step
    t1 = time.monotonic()
    s1 = _stage1(r, name, seq)
    r.timed("stage1", t1)
    if s1["status"] == "fail":
        r.out["stage1"] = {"status": "fail"}
        return r.failed(s1["failure"], "Could not set up the proof")
    r.out["stage1"] = {
        k: s1[k]
        for k in ("status", "proven", "unproven", "unproven_signals", "unpaired_outputs")
        if k in s1
    }
    points: list[equiv_script.Point] = []
    n_ff = n_ff_gate = 0
    proven1 = s1["status"] == "proven" and not s1["unpaired_outputs"]
    if proven1 or s1["status"] != "timeout":
        try:
            points, n_ff, n_ff_gate = equiv_parse.equiv_points(
                (scratch / "equiv.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError, KeyError, IndexError):
            return r.verdict("error", "yosys_failed", "The proof model could not be read.")
    flops = bool(n_ff or n_ff_gate) or s1["status"] == "timeout"
    cycles = depth if flops else 2  # no flip-flops: one compared cycle is the whole story
    rtl = equiv_script.stash(equiv_script.gold(name, "rtl"), name, "gold")
    gate = equiv_script.stash(equiv_script.gate(name, "netlist.v"), name, "gate")

    # stage 2: does the RTL rely on initial values?
    if s1["rtl_has_init"]:
        done = _initial_values(r, name, rtl, cycles, s1.get("rtl_has_memory", False))
        if done is not None:
            return done

    if proven1:
        return _proven_path(r, name, s1, points, flops, gate, seq, cycles)
    return _kinduction_path(r, name, s1, rtl, gate, cycles)


MEMORY_HINT = (
    " A memory's contents cannot be set by rst_n: do not rely on its starting contents (a chip's "
    "memory starts unknown), write the values after reset instead."
)


def _initial_values(
    r: _Run, name: str, rtl: str, cycles: int, has_memory: bool
) -> dict[str, Any] | None:
    """Stage 2. None when the RTL's initial values change nothing after the reset cycle (the
    check goes on), else the verdict. Never skipped: every way this stage can end without a
    proof is `not_proven` or `error`, never a pass."""
    t2 = time.monotonic()
    code, log = r.yosys(r.scratch, equiv_script.state_check(name), "initstate", r.clock.left())
    state = equiv_parse.state_result(log)
    if code != 0 and state != "unusable":
        state = "unreadable"
    r.timed("initdep", t2)
    if state == "equal":
        return None
    stopped = code is None
    names = ""
    if state == "differ":
        found = equiv_parse.parse_cex(log)["first_mismatch"]
        if found is not None:
            regs = dict.fromkeys(  # register names without their bit index, each once
                re.sub(r"(\[\d+\])+$", "", str(x["signal"])) for x in found["differs"]
            )
            names = ", ".join(list(regs)[:6])
    # Some register keeps an initial value that reset does not set (or the state check could not
    # be read). Look for the output it changes in the first `cycles` cycles from reset.
    noinit = equiv_script.stash(equiv_script.gold(name, "rtl_noinit"), name, "gate")
    d = _miter(r, "initdep", rtl, noinit, "undef", "base", cycles)
    r.timed("initdep", t2)
    if d["status"] == "fail":
        return r.failed(d["failure"], "Could not check the RTL's initial values")
    if d["status"] == "cex":
        fm = _mismatch(d)
        if fm is None:
            return _unreadable_cex(r, d, "between the RTL and the RTL without its initial values")
        r.out.update(first_mismatch=fm, trace=_trace(d["trace"], fm))
        out = ", ".join(str(x["signal"]) for x in fm["differs"][:8])
        return r.verdict(
            "not_equivalent",
            "rtl_initial_value",
            "The RTL relies on initial values (`reg x = ...`, `initial`, Amaranth "
            "reset_less or init, memory contents) that a chip does not have: at "
            f"{_when(fm['cycle'])} {out} depend on them. Reset those registers with rst_n "
            f"instead.{MEMORY_HINT if has_memory else ''}",
        )
    if d.get("stopped") or stopped:
        r.out["time_limit_hit"] = True
    if state == "unusable" and not (d.get("stopped") or stopped):
        r.out["cycles_covered"] = d["covered"]
        return r.verdict(
            "not_proven",
            "state_check_unusable",
            "Could not prove equivalence: the RTL has initial values and a register the check "
            "cannot look at (it could not be exposed), so the check cannot tell whether the "
            f"initial values change anything. No output differed in the first {d['covered']} "
            "cycles from reset. Reset every register with rst_n and remove the initial values.",
            cycles_covered=d["covered"],
        )
    which = f" ({names})" if names else ""
    limit = " The check ran out of time." if d.get("stopped") or stopped else ""
    return r.verdict(
        "not_proven",
        "time_limit" if limit else "initial_values",
        "Could not prove equivalence: the RTL gives initial values to registers that reset does "
        f"not set{which}. A chip has no initial values, and no output differed in the first "
        f"{d['covered']} cycles from reset, so the check cannot say whether the gates match. "
        f"Reset those registers with rst_n.{MEMORY_HINT if has_memory else ''}{limit}",
        cycles_covered=d["covered"],
    )


def _proven_path(
    r: _Run,
    name: str,
    s1: dict[str, Any],
    points: list[equiv_script.Point],
    flops: bool,
    gate: str,
    seq: int,
    cycles: int,
) -> dict[str, Any]:
    """Stage 1 proved every point: the base case (stage 3), then the referee (stage 4)."""
    t3 = time.monotonic()
    # No flip-flops on either side: stage 1 is already a complete combinational proof.
    base: dict[str, Any] = _base(r, points, seq + 2) if flops else {"status": "ok", "steps": 0}
    r.timed("base", t3)
    r.out["base"] = {k: v for k, v in base.items() if k != "failure"}
    if base["status"] == "fail":
        return r.failed(base["failure"], "Could not check the base case")
    x_outputs = base["status"] == "cex" and base["run"] == "X" and base["outputs_differ"]
    if x_outputs:
        r.out["first_mismatch"] = {"cycle": base["cycle"], "differs": base["differs"]}
        pins = [d for d in base["differs"] if str(d["signal"]).startswith(equiv_script.OUTPUTS)]
        r.out["trace"] = [
            {
                "cycle": base["cycle"],
                "inputs": {k: str(v) for k, v in base["inputs"].items() if v is not None},
                "rtl": {str(d["signal"]): str(d["rtl"]) for d in pins[:8]},
                "netlist": {str(d["signal"]): str(d["netlist"]) for d in pins[:8]},
            }
        ]
        return r.verdict(
            "not_equivalent",
            None,
            f"At {_when(base['cycle'])}: {_points(base['differs'])}. The RTL defines these "
            "values even with every register powering up x, but the netlist differs from them for "
            "some power-up state of its flip-flops (the netlist values shown are for one such "
            "state).",
        )
    t4 = time.monotonic()
    # The RTL without its initial values: stage 2 showed they change nothing from cycle 2 on, and
    # an `init` would fix a power-up value that the RTL's x power-up must leave open.
    noinit = equiv_script.stash(equiv_script.gold(name, "rtl_noinit"), name, "gold")
    ref = _miter(r, "bmc", noinit, gate, "per_side", "base", cycles)
    r.timed("stage4", t4)
    if ref["status"] == "fail":
        return r.failed(ref["failure"], "Could not run the check from reset")
    if ref["status"] == "cex":
        fm = _mismatch(ref)
        if fm is None:
            return _unreadable_cex(r, ref, "from reset")
        r.out.update(first_mismatch=fm, trace=_trace(ref["trace"], fm))
        r.out["internal_inconsistency"] = base["status"] == "ok"
        hint = _x_hint(fm)
        return r.verdict(
            "not_equivalent",
            "internal_inconsistency" if base["status"] == "ok" else None,
            f"At {_when(fm['cycle'])}: {_points(fm['differs'])}.{hint}",
        )
    r.out["cycles_covered"] = ref["covered"]
    if base["status"] == "cex":
        what = (
            f"internal signal(s) {_points(base['differs'])} differ from the RTL at "
            f"{_when(base['cycle'])} for some power-up state of the netlist's flip-flops, although "
            "the RTL defines them with every register at x"
            if base["run"] == "X"
            else f"{_points(base['differs'])} differ from the RTL at {_when(base['cycle'])} when "
            "the netlist's registers power up equal to the RTL registers they are paired with; "
            "these values depend on registers that reset does not set (the RTL simulation shows "
            "x there)"
        )
        return r.verdict(
            "not_proven",
            "base_case",
            f"Could not prove equivalence: {what}; no output the RTL defines differed in the "
            f"first {ref['covered']} cycles from reset.",
        )
    if ref.get("stopped"):  # the independent check did not finish: that is not a pass
        r.out["time_limit_hit"] = True
        return r.verdict(
            "not_proven",
            "time_limit",
            "Could not prove equivalence: the induction proof closed, but the independent check "
            f"from reset ran out of time after {ref['covered']} of {cycles} cycles "
            f"({SOLVER_HINT}).",
        )
    cov = f"{ref['covered']} cycles"
    return r.verdict(
        "equivalent",
        None,
        f"{s1['proven']} matched points proven by induction, the base case checked for every "
        f"power-up state ({seq + 2} cycles); an independent check from reset found no difference "
        f"in {cov}.",
        method="equiv_induct",
        cycles_covered=ref["covered"],
    )


def _x_hint(fm: dict[str, Any]) -> str:
    if any(d["netlist"] in ("x", None) for d in fm["differs"]):
        return (
            " The netlist value depends on the power-up state there: a register that reset does "
            "not set (or that the RTL gives an initial value instead of a reset)."
        )
    return ""


def _kinduction_path(
    r: _Run, name: str, s1: dict[str, Any], rtl: str, gate: str, cycles: int
) -> dict[str, Any]:
    """Stage 1 did not prove every point: k-induction on the outputs, twice (stage 4).

    The first run starts every flip-flop of both sides at x, and x spreads through the netlist
    gate by gate: a mux whose two inputs are equal gives x for an x select, where every real
    power-up state gives the same value. So a difference found there whose netlist bits are all
    x is not reported as it is: the bounded search with the per-side power-up of the referee
    (RTL registers x, netlist flip-flops any defined value: exact) runs over the same cycles,
    and only a difference it finds is `not_equivalent`. A netlist bit that is defined there and
    differs is a difference for every power-up state."""
    t4 = time.monotonic()
    res: dict[str, dict[str, Any]] = {}
    runs: tuple[tuple[str, str, str], ...] = (
        ("kind_x", "undef", "xstep"),
        ("kind_zero", "zero", "defstep"),
    )
    if s1["status"] == "timeout":  # SAT is struggling: only look for a counterexample from reset
        runs = (("kind_x", "undef", "base"),)
    unproven = s1.get("unproven_signals") or []
    hint = f" Unproven points: {', '.join(unproven[:8])}." if unproven else ""
    for tag, init, step in runs:
        got = _miter(r, tag, rtl, gate, init, step, cycles)
        res[tag] = {k: v for k, v in got.items() if k not in ("trace", "failure")}
        if got["status"] == "fail":
            r.timed("stage4", t4)
            return r.failed(got["failure"], "Could not run the check from reset")
        if got["status"] == "cex":
            r.timed("stage4", t4)
            fm = _mismatch(got)
            if fm is None:
                return _unreadable_cex(r, got, "from reset")
            if tag == "kind_x" and all(d["netlist"] in ("x", None) for d in fm["differs"]):
                # only x spreading shows it: look again with each flip-flop at a defined value
                noinit = equiv_script.stash(equiv_script.gold(name, "rtl_noinit"), name, "gold")
                got = _miter(r, "kind_x_check", noinit, gate, "per_side", "base", cycles)
                res[tag] = {k: v for k, v in got.items() if k not in ("trace", "failure")}
                if got["status"] == "fail":
                    return r.failed(got["failure"], "Could not run the check from reset")
                if got["status"] != "cex":  # not confirmed: the induction did not close
                    continue
                fm = _mismatch(got)
                if fm is None:
                    return _unreadable_cex(r, got, "from reset")
            r.out.update(first_mismatch=fm, trace=_trace(got["trace"], fm))
            if tag == "kind_x":
                return r.verdict(
                    "not_equivalent",
                    None,
                    f"At {_when(fm['cycle'])}: {_points(fm['differs'])}.{_x_hint(fm)}{hint}",
                )
            return r.verdict(
                "not_proven",
                "power_up_dependent",
                "Could not prove equivalence: with every register powering up at 0 the netlist "
                f"differs at {_when(fm['cycle'])}: {_points(fm['differs'])}. The RTL value there "
                "depends on registers that reset does not set (RTL simulation shows x), so this "
                "is a real bug or a different power-up value. Reset those registers with "
                f"rst_n.{hint}",
            )
    r.timed("stage4", t4)
    if all(v["status"] == "proven" for v in res.values()):
        length = max(v["length"] or 0 for v in res.values())
        return r.verdict(
            "equivalent",
            None,
            f"k-induction from reset closed at length {length} (outputs, every power-up state; "
            "all-zero power-up for values that depend on it).",
            method=f"k-induction length {length}",
            cycles_covered=min(v["covered"] for v in res.values()),
        )
    covered = min(v["covered"] for v in res.values())
    stopped = any(v.get("stopped") for v in res.values())
    r.out["cycles_covered"] = covered
    r.out["time_limit_hit"] = stopped or s1["status"] == "timeout"
    if s1["status"] == "timeout":
        why, reason = (
            "the induction proof ran out of time (arithmetic such as multipliers is hard for "
            "the solver)",
            "time_limit",
        )
    elif s1.get("unproven"):
        why, reason = (
            f"{s1['unproven']} point(s) could not be proven ({', '.join(unproven[:8])})",
            "unproven_points",
        )
    else:
        why, reason = f"outputs not paired: {s1['unpaired_outputs']}", "unpaired_outputs"
    if stopped and reason != "time_limit":
        why += "; the check from reset also ran out of time"
    return r.verdict(
        "not_proven",
        reason,
        f"Could not prove equivalence: {why}; no difference was found in the first {covered} "
        f"cycles from reset{' (stopped by the time limit)' if stopped else ''}.",
    )


# -- the verb ---------------------------------------------------------------------------------


def _deadline(request: dict[str, Any]) -> float:
    value = request.get("equiv_deadline_s", DEADLINE_S)
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        raise ValueError("equiv_deadline_s must be a number above 0")
    return float(value)


def run(request: dict[str, Any]) -> dict[str, Any]:
    """The `equiv` verb: emit, the production synthesis, then `check` on the netlist it wrote.

    Request fields: those of `synth` (`name`, `frontend`, `author`, `run_dir`, `clock_period_ns`,
    `stop_area_um2`, `timeout_s` = the time limit for the synthesis part) and `equiv_deadline_s`
    (the limit for the check; default 300). Returns the emit result plus `synth` (the `synth`
    verb's entry, None unless emit was ok) and `equiv` (the verdict dict, None unless synthesis
    ran to the end and left no check problem).
    """
    started = time.monotonic()
    name, frontend, _author, run_dir = emit._check_request(request)
    clock_period_ns = synth._number(request, "clock_period_ns")
    stop_area_um2 = synth._number(request, "stop_area_um2")
    timeout_s = synth._number(request, "timeout_s")
    deadline_s = _deadline(request)
    result = emit.emit(request)
    result["synth"] = None
    result["equiv"] = None
    if not result.get("ok"):
        return result
    deadline = started + max(timeout_s - synth.RESERVE_S, 1.0)
    outcome = synth.synthesize(name, frontend, run_dir, clock_period_ns, stop_area_um2, deadline)
    if isinstance(outcome.get("message"), str):
        outcome["message"] = clip(outcome["message"], MESSAGE_MAX)
    result["synth"] = outcome
    # A netlist the synthesis step itself would fail (a loop, a forbidden cell) is not checked.
    if outcome["state"] != "ran" or outcome["checks"] or outcome["forbidden_cells"]:
        return result
    out_dir = run_dir / "out"
    netlist = out_dir / synth.SYNTH_DIR / f"tt_um_{name}.v"
    result["equiv"] = check(
        out_dir,
        name,
        netlist,
        out_dir / EQUIV_DIR,
        deadline_s=deadline_s,
        strict_core=frontend != "myhdl",
    )
    return result


__all__ = ["check", "run", "run_yosys"]
