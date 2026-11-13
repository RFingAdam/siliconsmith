# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

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
constructs that a simulator and Yosys read differently. NOT detected: an out-of-range index or a
division by zero, and anything that depends on simulation event order; gate-level simulation
(`run_tests` with `gl=true`) is the check against simulation behaviour.

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
    seconds      {stage1, initdep, base, stage4, total}: seconds per stage that ran
    stage1       {status, proven, unproven, unproven_signals, unpaired_outputs} or None
    base         {status, run, cycle, differs} or None
    first_mismatch  {cycle, differs: [{signal, rtl, netlist}]} or None. Cycle 1 is power-up with
                 rst_n low; outputs are compared from cycle 2 (but an output the RTL defines and
                 the netlist leaves unknown is reported from cycle 1).
    trace        per cycle up to the mismatch: {cycle, inputs{rst_n, ui_in, uio_in},
                 rtl{uo_out, uio_out, uio_oe}, netlist{...}} as bit strings (x = unknown)
    internal_inconsistency  true when the induction and the referee disagree
    cycles_covered  cycles from reset searched without a difference (not_proven)
    time_limit_hit  true when a Yosys step was stopped at its time limit
    detail       a few Yosys error lines (error and guard refusals)
    netlist_sha256  sha256 of the netlist bytes that were checked (the private copy)

Time: every Yosys call is a subprocess with its own limit (`budget_s`, 120 s; Yosys's own
`sat -timeout` is ignored), and the whole check has a deadline (`deadline_s`, 300 s). Each call
runs in the private `scratch` folder, which holds copies of the inputs, so parallel checks never
share files.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from siliconsmith.worker import emit, equiv_parse, equiv_script, equiv_sv, myhdl_map, synth
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
NETLIST_MAX_BYTES = 16 * 1024 * 1024
NOT_A_PASS = "This is NOT a pass."
EQUIV_DIR = "equiv"

# A Yosys runner: (folder, script text, tag, time limit in seconds) -> (exit code, log text).
# Exit code None means the call was stopped at the limit.
YosysRunner = Callable[[Path, str, str, float], tuple[int | None, str]]


def run_yosys(cwd: Path, script: str, tag: str, timeout: float) -> tuple[int | None, str]:
    """Run one Yosys script in `cwd`; the log is `<tag>.log` next to it. Stops at `timeout`."""
    (cwd / f"{tag}.ys").write_text(script, encoding="utf-8")
    log = cwd / f"{tag}.log"
    log.unlink(missing_ok=True)
    if timeout <= 0:
        return None, ""
    try:
        proc = subprocess.run(
            ["yosys", "-q", "-l", log.name, "-s", f"{tag}.ys"],
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
        code: int | None = proc.returncode
        err = proc.stderr
    except subprocess.TimeoutExpired:
        emit._reap_processes()
        code, err = None, ""
    text = log.read_text(errors="replace") if log.exists() else ""
    return code, text + err


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

    def __init__(self, scratch: Path, run: YosysRunner, clock: Clock, t0: float) -> None:
        self.scratch, self.yosys, self.clock, self.t0 = scratch, run, clock, t0
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


def _base(r: _Run, points: list[equiv_script.Point], steps: int) -> dict[str, Any]:
    """Base case of the stage-1 induction (X run and DEF run). `status`: ok | cex | fail."""
    il = (r.scratch / "equiv.il").read_text(encoding="utf-8", errors="replace")
    (r.scratch / "base.il").write_text(equiv_parse.base_model(il, points), encoding="utf-8")
    code, log = r.yosys(r.scratch, equiv_script.base_script(points, steps), "base", r.clock.left())
    if code != 0:
        failure = equiv_parse.guard_failure(code, log) or {
            "verdict": "error",
            "reason": "yosys_failed",
            "message": "the base-case proof failed",
            "detail": [],
        }
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
    """One `sat` run on a miter. `status`: cex | proven | no_cex | fail."""
    script = equiv_script.miter(golds, gates, tag, init, induct, depth)
    code, log = r.yosys(r.scratch, script, tag, r.clock.left())
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


def _myhdl_refusal(frontend: str, work: Path = Path("/work")) -> str | None:
    """For a MyHDL project: a plain sentence if a process in `hdl/` waits for a level signal."""
    if frontend != "myhdl":
        return None
    found = myhdl_map.level_sensitive(myhdl_map.read_functions(work))
    if found is None:
        return None
    return (
        f"the MyHDL source ({found['file']}, function {found['function']}, line {found['line']}) "
        f"uses {found['what']}, a process that waits for a level signal: a simulator only reruns "
        "it when that signal changes, the gates do not care. Use `@always_comb` for combinational "
        "logic, or `@always_seq` / `@always(clk.posedge)` for clocked logic."
    )


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
    source_refusal: str | None = None,
) -> dict[str, Any]:
    """Check the RTL in `rtl_dir` (`project.v`, `<name>.v`) against the netlist FILE `netlist`.

    `strict_core`: judge `<name>.v` by the two rules that only make sense for a core that does not
    list level signals itself (every level-sensitive `always @(a or b)` and every function or
    task without `automatic` is refused). True for Verilog (hand-written) and Amaranth (its
    combinational logic is `@*`); False for MyHDL, whose `@always_comb` is written as
    `always @(a, b)`: its level-sensitive processes are judged in the `hdl/` Python source by the
    caller, who passes what it found as `source_refusal` (a plain sentence, or None).

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
            source_refusal,
        )
    except Exception as exc:
        failed = _Run(scratch, yosys, Clock(budget_s, deadline_s), t0)
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
    source_refusal: str | None,
) -> dict[str, Any]:
    scratch.mkdir(parents=True, exist_ok=True)
    r = _Run(scratch, yosys, Clock(budget_s, deadline_s), t0)
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
    if source_refusal is not None:
        return r.verdict(
            "not_proven",
            "guard_sim_synth_differ",
            f"Could not prove equivalence: {source_refusal}",
        )
    try:
        sources = [
            (fname, (scratch / fname).read_bytes().decode("latin-1"))
            for fname in ("project.v", f"{name}.v")
        ]
        found = equiv_sv.refusal(sources, {f"{name}.v"} if strict_core else set())
        if found is not None:
            return r.verdict(
                "not_proven",
                "guard_include" if found.code == "include" else "guard_sim_synth_differ",
                f"Could not prove equivalence: {equiv_sv.plain_message(found)}",
            )
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
        return _proven_path(r, name, s1, points, flops, rtl, gate, seq, cycles)
    return _kinduction_path(r, s1, rtl, gate, cycles)


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
    rtl: str,
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
    if base["status"] == "cex" and base["run"] == "X" and base["outputs_differ"]:
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
            "values but the netlist does not match them for some power-up state (x means the "
            "value depends on the power-up state: a register that reset does not set).",
        )
    t4 = time.monotonic()
    ref = _miter(r, "bmc", rtl, gate, "undef", "base", cycles)
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
            f"{_when(base['cycle'])} for some power-up state"
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
            f"from reset ran out of time after {ref['covered']} of {cycles} cycles.",
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
    r: _Run, s1: dict[str, Any], rtl: str, gate: str, cycles: int
) -> dict[str, Any]:
    """Stage 1 did not prove every point: k-induction on the outputs, twice (stage 4)."""
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
        source_refusal=_myhdl_refusal(frontend) if frontend == "myhdl" else None,
    )
    return result


__all__ = ["check", "run", "run_yosys"]
