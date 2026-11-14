# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The `check_equivalence` tool with a fake runner that returns canned worker results."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from test_synthesize import NETLIST, SynthRunner, make, outcome, synth_part, worker_result

from siliconsmith.ledger import load_ledger
from siliconsmith.status import compute_status
from siliconsmith.tools import check_equivalence as tool
from siliconsmith.worker import limits as lim

SHA = hashlib.sha256(NETLIST.encode()).hexdigest()
NOT_A_PASS = "This is NOT a pass."


def equiv_part(verdict: str = "equivalent", **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "verdict": verdict,
        "ok": verdict == "equivalent",
        "reason": None,
        "method": "equiv_induct" if verdict == "equivalent" else None,
        "message": "24 matched points proven by induction.",
        "seconds": {"stage1": 1.8, "base": 1.2, "stage4": 5.5, "total": 8.5},
        "stage1": {"status": "proven", "proven": 24, "unproven": 0},
        "base": {"status": "ok"},
        "first_mismatch": None,
        "trace": [],
        "internal_inconsistency": False,
        "cycles_covered": 32,
        "time_limit_hit": False,
        "detail": [],
        "netlist_sha256": SHA,
    }
    base.update(fields)
    return base


def result_for(equiv: dict[str, Any] | None, **fields: Any) -> dict[str, Any]:
    return worker_result(verb="equiv", synth=synth_part(netlist_sha256=SHA), equiv=equiv, **fields)


def mismatch(signal: str = "uo_out[3]", rtl: str = "0", netlist: str = "1") -> dict[str, Any]:
    return {"cycle": 5, "differs": [{"signal": signal, "rtl": rtl, "netlist": netlist}]}


def trace(cycles: int = 5) -> list[dict[str, Any]]:
    return [
        {
            "cycle": c,
            "inputs": {"rst_n": "1" if c > 1 else "0", "ui_in": "00000001", "uio_in": "00000000"},
            "rtl": {"uo_out": f"{c - 1:08b}", "uio_out": "00000000", "uio_oe": "00000000"},
            "netlist": {
                "uo_out": f"{(c - 1) | (8 if c == cycles else 0):08b}",
                "uio_out": "00000000",
                "uio_oe": "00000000",
            },
        }
        for c in range(1, cycles + 1)
    ]


def run_tool(tmp_path: Path, equiv: dict[str, Any] | None, **fields: Any) -> tuple[Any, Any, Any]:
    runner = SynthRunner(outcome(result_for(equiv, **fields)))
    ctx, project = make(tmp_path, runner)
    IMAGES[project.dir] = ctx.image
    return tool.check_equivalence(ctx, "chip"), project, runner


IMAGES: dict[Path, str] = {}


def states_of(project: Any) -> dict[str, str]:
    return {s.key: s.state for s in compute_status(project, IMAGES[project.dir]).steps}


# -- the three verdicts --------------------------------------------------------------------------


def test_equivalent_is_the_only_ok(tmp_path: Path) -> None:
    result, project, runner = run_tool(tmp_path, equiv_part())
    assert result.ok, result.summary
    lines = result.summary.splitlines()
    assert lines[0] == (
        "Equivalent: the sky130 netlist does the same as the RTL (proven by induction over every "
        "register, plus a base case for every power-up state)."
    )
    assert "Not checked: bugs in the RTL itself" in result.summary
    assert "run_tests with gl=True" in result.summary
    assert SHA[:12] in result.summary
    details = result.details
    assert details["stage"] == "equivalent" and details["method"] == "equiv_induct"
    assert details["netlist_sha256"] == SHA and details["synth"]["flip_flops"] == 8
    assert not (project.dir / "build" / "synth").exists()  # no netlist is kept
    assert not list((project.dir / "build" / ".ss").glob("*"))
    ((verb, request, run_project, timeout),) = runner.calls
    assert (verb, run_project) == ("equiv", project.dir)
    assert timeout == 900.0 + 300.0 + 60.0 == tool.EQUIV_TIMEOUT_S
    assert request["timeout_s"] == 900.0 and request["equiv_deadline_s"] == 300.0
    assert request["clock_period_ns"] == 100 and request["frontend"] == "verilog"


def test_k_induction_is_named(tmp_path: Path) -> None:
    result, _, _ = run_tool(tmp_path, equiv_part(method="k-induction length 4"))
    assert result.ok
    assert "k-induction length 4 from reset" in result.summary.splitlines()[0]


def test_not_equivalent_names_the_bit_the_cycle_the_values_and_the_trace(tmp_path: Path) -> None:
    equiv = equiv_part(
        "not_equivalent",
        message="At cycle 5 (cycle 1 is power-up, with rst_n low): uo_out[3] (RTL 0x00, "
        "netlist 0x08).",
        first_mismatch=mismatch(),
        trace=trace(),
    )
    result, _, _ = run_tool(tmp_path, equiv)
    assert not result.ok
    lines = result.summary.splitlines()
    assert lines[0].startswith("NOT equivalent: At cycle 5")
    assert "uo_out[3]" in lines[0]
    assert any("cycle 1 is power-up" in ln for ln in lines[1:])
    row = next(ln for ln in lines if ln.startswith("  cycle 5:"))
    assert "rst_n=1 ui_in=0x01 uio_in=0x00" in row
    assert "uo_out RTL 0x04 netlist 0x0c" in row
    assert result.details["stage"] == "not_equivalent"
    assert result.details["first_mismatch"]["differs"][0]["signal"] == "uo_out[3]"
    hint = " ".join(result.next_hints)
    assert "uo_out[3] at cycle 5" in hint and "gl=True" in hint and "synthesize" in hint


def test_a_power_up_difference_shows_its_bits_in_the_trace(tmp_path: Path) -> None:
    equiv = equiv_part(
        "not_equivalent",
        message="At cycle 1 (power-up, with rst_n low): uio_oe[0] (RTL 0, netlist x).",
        first_mismatch={
            "cycle": 1,
            "differs": [{"signal": "uio_oe[0]", "rtl": "0", "netlist": "x"}],
        },
        trace=[
            {
                "cycle": 1,
                "inputs": {"rst_n": "0"},
                "rtl": {"uio_oe[0]": "0"},
                "netlist": {"uio_oe[0]": "x"},
            }
        ],
    )
    result, _, _ = run_tool(tmp_path, equiv)
    assert "  cycle 1: rst_n=0 -> uio_oe[0] RTL 0 netlist x" in result.summary.splitlines()


def test_a_netlist_x_adds_the_power_up_hint(tmp_path: Path) -> None:
    equiv = equiv_part(
        "not_equivalent",
        message="At cycle 1 (power-up, with rst_n low): uio_oe[0] (RTL 0, netlist x).",
        first_mismatch={
            "cycle": 1,
            "differs": [{"signal": "uio_oe[0]", "rtl": "0", "netlist": "x"}],
        },
    )
    result, _, _ = run_tool(tmp_path, equiv)
    assert not result.ok
    assert any("depends on the power-up state" in h and "rst_n" in h for h in result.next_hints)


def test_rtl_initial_value_says_reset_those_registers(tmp_path: Path) -> None:
    equiv = equiv_part(
        "not_equivalent",
        reason="rtl_initial_value",
        message="The RTL relies on initial values that a chip does not have. Reset those "
        "registers with rst_n instead.",
        first_mismatch=mismatch("uo_out[0]", "1", "x"),
    )
    result, _, _ = run_tool(tmp_path, equiv)
    assert not result.ok and "Reset those registers with rst_n" in result.summary
    assert "with rst_n instead of an initial value" in result.next_hints[0]


@pytest.mark.parametrize(
    ("reason", "word"),
    [
        ("guard_clocking", "falling-edge"),
        ("guard_tristate", "uio_oe"),
        ("guard_async_set_reset", "one reset only"),
        ("time_limit", "ran out of time"),
        ("unproven_points", "could not decide"),
        ("power_up_dependent", "Reset every register"),
    ],
)
def test_not_proven_says_why_and_is_never_a_pass(tmp_path: Path, reason: str, word: str) -> None:
    equiv = equiv_part(
        "not_proven",
        reason=reason,
        message=f"The check could not decide ({reason}). {NOT_A_PASS}",
        cycles_covered=12,
        time_limit_hit=reason == "time_limit",
        stage1={"status": "unproven", "proven": 20, "unproven": 4},
    )
    result, project, _ = run_tool(tmp_path, equiv)
    assert not result.ok
    assert result.summary.splitlines()[0].startswith("Not proven: ")
    assert NOT_A_PASS in result.summary
    assert "first 12 cycles from reset" in result.summary
    assert result.details["stage"] == "not_proven" and result.details["reason"] == reason
    assert word in " ".join(result.next_hints) + result.summary
    assert states_of(project)["equivalence"] == "fail"  # recorded, current, and never a pass
    if reason == "time_limit":
        assert "time limit" in result.summary


def test_not_proven_without_the_sentence_still_says_it(tmp_path: Path) -> None:
    # The worker always adds the sentence; the host would still not print a pass without it.
    equiv = equiv_part("not_proven", reason="unproven_points", message="Some points are open.")
    result, _, _ = run_tool(tmp_path, equiv)
    assert not result.ok and "Not proven:" in result.summary
    assert "NOT a pass" in result.summary


def test_a_failed_check_is_an_error_result_not_a_pass(tmp_path: Path) -> None:
    equiv = equiv_part(
        "error",
        reason="structure",
        message="Could not set up the proof: a combinational loop.",
        detail=["ERROR: Found 1 problems in 'check -assert'."],
    )
    result, _, _ = run_tool(tmp_path, equiv)
    assert not result.ok
    assert result.summary.splitlines()[0].startswith("Equivalence check failed: ")
    assert "Found 1 problems" in result.summary
    assert result.details["stage"] == "equiv_error"


# -- the ledger and the status row ---------------------------------------------------------------


def test_the_ledger_stores_the_verdict_the_method_the_hash_and_the_synth_digest(
    tmp_path: Path,
) -> None:
    result, project, _ = run_tool(tmp_path, equiv_part())
    assert states_of(project)["equivalence"] == "pass"
    (record,) = load_ledger(project.dir).ledger.runs
    assert record.tool == "check_equivalence" and record.ok and record.stage == "equivalent"
    assert record.equiv is not None
    assert (record.equiv.verdict, record.equiv.reason, record.equiv.method) == (
        "equivalent",
        None,
        "equiv_induct",
    )
    assert record.equiv.netlist_sha256 == SHA
    assert record.digests.synth is not None and record.sim is None and not record.gl
    assert result.ok


@pytest.mark.parametrize(
    ("verdict", "reason"),
    [
        ("not_equivalent", None),
        ("not_proven", "time_limit"),
        ("error", "yosys_failed"),
    ],
)
def test_a_result_that_is_not_equivalent_records_a_failed_run(
    tmp_path: Path, verdict: str, reason: str | None
) -> None:
    extra: dict[str, Any] = {}
    if verdict == "not_equivalent":
        extra["first_mismatch"] = mismatch()
    result, project, _ = run_tool(tmp_path, equiv_part(verdict, reason=reason, **extra))
    (record,) = load_ledger(project.dir).ledger.runs
    assert not result.ok and not record.ok
    assert record.equiv is not None and record.equiv.verdict == verdict
    assert record.stage == ("equiv_error" if verdict == "error" else verdict)


# -- the result is untrusted ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "equiv",
    [
        equiv_part(ok=False),  # ok that contradicts the verdict
        equiv_part("not_proven", ok=True, message=f"x {NOT_A_PASS}"),
        equiv_part(method=None),  # an equivalent verdict names its method
        equiv_part(reason="base_case"),  # ... and has no reason
        equiv_part(first_mismatch=mismatch()),  # ... and no mismatch
        equiv_part(netlist_sha256=None),  # ... and says which netlist it checked
        equiv_part(method="magic"),  # ... a method this tool knows
        equiv_part(stage1={"status": "unproven", "proven": 20, "unproven": 4}),  # a complete proof
        equiv_part(
            stage1={"status": "proven", "proven": 24, "unproven": 0, "unpaired_outputs": ["uo_out"]}
        ),
        equiv_part(stage1=None),
        equiv_part(base={"status": "cex", "run": "DEF", "cycle": 2}),
        equiv_part(base=None),
        equiv_part("not_equivalent"),  # a difference names where
        equiv_part(verdict="proven"),
        equiv_part(message="x" * (lim.MESSAGE_MAX + 1)),
        equiv_part("error", detail=["x"] * (lim.MAX_EQUIV_DETAIL + 1)),
        equiv_part("error", detail=["x" * (lim.EQUIV_DETAIL_LINE_MAX + 1)]),
        equiv_part(trace=trace(lim.MAX_EQUIV_TRACE + 1)),
        equiv_part(
            "not_equivalent",
            first_mismatch=mismatch("s" * (lim.EQUIV_SIGNAL_MAX + 1)),
        ),
        equiv_part(
            "not_equivalent",
            first_mismatch={
                "cycle": 2,
                "differs": [{"signal": "uo_out[0]", "rtl": "0", "netlist": "1"}]
                * (lim.MAX_EQUIV_DIFFS + 1),
            },
        ),
        equiv_part(
            "not_equivalent",
            first_mismatch=mismatch(rtl="0" * (lim.EQUIV_VALUE_MAX + 1)),
        ),
        equiv_part(cycles_covered=-1),
        equiv_part(ok="yes"),  # strict types
    ],
    ids=lambda e: "-".join(str(e["verdict"]).split()),
)
def test_a_contradicting_or_oversized_verdict_is_not_believed(
    tmp_path: Path, equiv: dict[str, Any]
) -> None:
    result, project, _ = run_tool(tmp_path, equiv)
    assert not result.ok
    assert "unreadable result" in result.summary
    assert "Equivalent:" not in result.summary
    assert states_of(project)["equivalence"] != "pass"


def test_a_missing_verdict_is_not_believed(tmp_path: Path) -> None:
    result, _, _ = run_tool(tmp_path, None)
    assert not result.ok and "no verdict" in result.summary


def test_the_verdict_must_be_about_the_netlist_that_was_synthesized(tmp_path: Path) -> None:
    for equiv in (
        equiv_part(netlist_sha256="1" * 64),
        equiv_part("not_proven", netlist_sha256="1" * 64),
    ):
        where = tmp_path / str(equiv["verdict"])
        where.mkdir()
        result, _, _ = run_tool(where, equiv)
        assert not result.ok and "changed while it was being checked" in result.summary


def test_the_synthesis_hash_must_match_the_files(tmp_path: Path) -> None:
    runner = SynthRunner(
        outcome(worker_result(verb="equiv", synth=synth_part(netlist_sha256="2" * 64),
                              equiv=equiv_part())),
    )  # fmt: skip
    ctx, _ = make(tmp_path, runner)
    result = tool.check_equivalence(ctx, "chip")
    assert not result.ok and "changed while it was being checked" in result.summary


# -- what stops the check before there is a verdict ------------------------------------------------


def test_synthesis_problems_stop_the_check(tmp_path: Path) -> None:
    check = {"kind": "comb_loop", "signal": "x", "file": "src/chip.v", "line": 7, "message": "loop"}
    runner = SynthRunner(
        outcome(worker_result(verb="equiv", synth=synth_part(checks=[check]), equiv=None))
    )
    ctx, project = make(tmp_path, runner)
    result = tool.check_equivalence(ctx, "chip")
    assert not result.ok and result.details["stage"] == "synth_failed"
    assert result.summary.startswith("Equivalence was not checked: synthesis found a problem")
    assert "src/chip.v:7: comb loop" in result.summary
    (record,) = load_ledger(project.dir).ledger.runs
    assert record.stage == "synth_failed" and record.equiv is None


def test_a_forbidden_cell_stops_the_check(tmp_path: Path) -> None:
    part = synth_part(forbidden_cells=["sky130_fd_sc_hd__dfxtp_1"])
    ctx, _ = make(tmp_path, SynthRunner(outcome(worker_result(synth=part, equiv=None))))
    result = tool.check_equivalence(ctx, "chip")
    assert not result.ok and "dfxtp_1" in result.summary


@pytest.mark.parametrize(
    ("part", "stage", "word"),
    [
        (
            {"state": "yosys_failed", "log": ["src/chip.v:3: ERROR: bad"], "stat": None,
             "netlist": None, "netlist_sha256": None, "message": "Yosys"},
            "synth_failed",
            "src/chip.v:3: ERROR: bad",
        ),
        (
            {"state": "timeout", "stat": None, "netlist": None, "netlist_sha256": None,
             "message": "the technology mapping step ran out of time"},
            "timeout",
            "ran out of time",
        ),
        (
            {"state": "too_big", "estimate": {"generic_area_um2": 217168.2, "generic_cells": 9,
                                              "stop_area_um2": 29688.0},
             "stat": None, "netlist": None, "netlist_sha256": None},
            "too_big",
            "Too big for 1x1",
        ),
    ],
    ids=["yosys_failed", "timeout", "too_big"],
)  # fmt: skip
def test_a_synthesis_that_ended_early_is_reported_and_the_check_did_not_run(
    tmp_path: Path, part: dict[str, Any], stage: str, word: str
) -> None:
    runner = SynthRunner(
        outcome(worker_result(verb="equiv", synth=synth_part(**part), equiv=None)), netlist=None
    )
    ctx, project = make(tmp_path, runner)
    result = tool.check_equivalence(ctx, "chip")
    assert not result.ok
    assert result.summary.startswith("Equivalence was not checked: synthesis failed")
    assert word in result.summary and result.details["stage"] == stage
    assert "Equivalent" not in result.summary
    (record,) = load_ledger(project.dir).ledger.runs
    assert record.stage == stage and not record.ok


def test_a_lint_failure_stops_before_synthesis_and_names_this_tool(tmp_path: Path) -> None:
    lint = [
        {"severity": "warning", "code": "WIDTHTRUNC", "file": "src/chip.v", "line": 3, "col": 5,
         "msg": "Operator ASSIGNW expects 4 bits", "detail": ""}
    ]  # fmt: skip
    result = worker_result(ok=False, stage="lint", lint=lint, lint_total=1, synth=None)
    ctx, project = make(tmp_path, SynthRunner(outcome(result)))
    got = tool.check_equivalence(ctx, "chip")
    assert not got.ok
    assert got.summary.startswith("check_equivalence stopped before the check")
    assert "src/chip.v:3" in got.summary
    assert all("emit_verilog" not in h or "check_equivalence" in h for h in got.next_hints)
    # A lint failure says nothing about the netlist: the equivalence row stays "not run".
    IMAGES[project.dir] = ctx.image
    assert states_of(project)["equivalence"] == "not_run"


def test_a_killed_container_is_a_timeout_and_not_a_pass(tmp_path: Path) -> None:
    runner = SynthRunner(outcome(None, timed_out=True, exit_code=137))
    ctx, project = make(tmp_path, runner)
    result = tool.check_equivalence(ctx, "chip")
    assert not result.ok and result.details["stage"] == "timeout"
    assert NOT_A_PASS in result.summary and "1260 s" in result.summary
    assert load_ledger(project.dir).ledger.runs[-1].stage == "timeout"


def test_a_spec_that_cannot_be_used_is_a_tool_error_and_runs_nothing(tmp_path: Path) -> None:
    runner = SynthRunner()
    ctx, project = make(tmp_path, runner)
    (project.dir / "spec.yaml").write_text("title: x\n")
    with pytest.raises(ToolError, match="cannot continue"):
        tool.check_equivalence(ctx, "chip")
    assert runner.calls == []


def test_the_clock_and_tile_come_from_the_spec_every_time(tmp_path: Path) -> None:
    runner = SynthRunner(outcome(result_for(equiv_part())))
    ctx, project = make(tmp_path, runner)
    spec = project.dir / "spec.yaml"
    spec.write_text(spec.read_text().replace("clock_hz: 10000000", "clock_hz: 50000000"))
    tool.check_equivalence(ctx, "chip")
    assert runner.calls[0][1]["clock_period_ns"] == 20
    assert '"CLOCK_PERIOD": 20' in (project.dir / "src" / "config.json").read_text()


def test_the_description_says_what_is_and_is_not_checked() -> None:
    text = tool.DESCRIPTION
    for phrase in (
        "Equivalent:",
        "NOT equivalent:",
        "Not proven:",
        "This is NOT a pass",
        "every power-up state",
        "reset those registers with rst_n",
        "bugs in the RTL itself",
        "gl=True",
        "tri-state",
        "300 s",
        "seconds for a small design",
    ):
        assert phrase in text, phrase
