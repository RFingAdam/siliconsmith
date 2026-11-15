# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The equivalence check's verdicts, with a fake Yosys that answers with saved real logs.

Each test names the scenario whose real logs it replays (see tests/fixtures/equiv/README.md) and
asserts the verdict the check must reach. Whatever the verdict, `ok` is true only for
`equivalent`: a guard refusal, a time limit, a power-up-dependent difference and a Yosys failure
are never a pass.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import equiv, equiv_parse, equiv_script

REAL_RUN_VERILATOR = equiv._run_verilator  # the autouse fixture replaces it
REAL_RUN_ICARUS = equiv._run_icarus  # this one too

FIX = REPO_ROOT / "tests" / "fixtures" / "equiv"
FIXTURES_MYHDL = REPO_ROOT / "tests" / "fixtures" / "equiv_myhdl"
NOT_A_PASS = "This is NOT a pass."

# What the referee's first call writes (`miter.il`, `equiv_script.miter_model`): a flattened miter
# with one RTL register (`gold.`) and one netlist flip-flop (`gate.`).
MITER_IL = """module \\miter
  wire input 1 \\in_clk
  wire width 8 \\gold.core.cnt
  wire width 8 \\gold.core.d
  wire \\gate._1_.IQ
  wire \\gate._1_.D
  cell $dff $flatten\\gold.\\core.$procdff$16
    parameter \\WIDTH 8
    parameter \\CLK_POLARITY 1'1
    connect \\Q \\gold.core.cnt
    connect \\D \\gold.core.d
    connect \\CLK \\in_clk
  end
  cell $_DFF_P_ $flatten\\gate.\\_1_.$auto$liberty.cc:255:create_ff$1519
    connect \\Q \\gate._1_.IQ
    connect \\D \\gate._1_.D
    connect \\C \\in_clk
  end
end
"""


class FakeYosys:
    """Stands in for `equiv.run_yosys`: each tag gets (exit code, scenario, log file)."""

    def __init__(self, model: str, **answers: tuple[int | None, str, str] | None) -> None:
        self.model = model  # the scenario whose equiv.json / equiv.il stage 1 "writes"
        self.answers = answers
        self.calls: list[tuple[str, float]] = []
        self.gate_read = "module \\tt_um_chip\n  wire width 1 \\a\n  connect \\a 1'0\nend\n"
        # what the "elab" call writes (no process); None: it writes nothing
        self.elab_il: str | None = "module \\tt_um_chip\nend\n"
        self.miter_il: str | None = MITER_IL  # what the referee's "*_model" call writes

    def __call__(self, cwd: Path, script: str, tag: str, timeout: float) -> tuple[int | None, str]:
        self.calls.append((tag, timeout))
        (cwd / f"{tag}.ys").write_text(script)
        if timeout <= 0:  # like the real runner: no time left, nothing runs
            return None, ""
        answer = self.answers.get(tag)
        if tag == "elab" and self.elab_il is not None:  # for the Verilator cross-check
            (cwd / "elab.il").write_text(self.elab_il)
        if tag == "elab":
            answer = answer or (0, "inline", "")
        if tag.endswith("_model"):  # the referee's miter, before its power-up wires are added
            if self.miter_il is not None:
                (cwd / "miter.il").write_text(self.miter_il)
            answer = answer or (0, "inline", "")
        if tag.endswith("_once"):  # the one-shot search: by default it runs out of time
            answer = answer or (None, "inline", "")
        if answer is None:
            raise AssertionError(f"unexpected Yosys call {tag!r}; calls so far: {self.calls}")
        code, scenario, name = answer
        if tag == "equiv":  # the netlist as Yosys read it (what stage 1 writes first)
            (cwd / "gate_read.il").write_text(self.gate_read)
        if tag == "equiv" and self.model:
            for f in ("equiv.json", "equiv.il"):
                shutil.copy(FIX / self.model / f, cwd / f)
        if scenario == "inline":  # a made-up one-line log, for failures with no saved log
            return code, name
        return code, (FIX / scenario / name).read_text()

    @property
    def tags(self) -> list[str]:
        return [t for t, _ in self.calls]


@pytest.fixture(autouse=True)
def no_verilator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ALWCOMBORDER pass runs Verilator and the tree checks Icarus too; unit tests do not
    have them (each is inconclusive)."""
    monkeypatch.setattr(equiv, "_run_verilator", lambda argv, cwd: None)  # cannot start
    monkeypatch.setattr(equiv, "_run_icarus", lambda argv, cwd: None)  # cannot start


# The tree Verilator dumps before V3Param (`--dump-tree-json`): by default one with no generate
# construct, so `equiv_tree.x_condition` finds nothing.
EARLY_STAGE = "Vtt_um_chip_008_linkinc.tree.json"
NO_GENERATE = json.dumps({"type": "NETLIST", "modulesp": []})


def write_trees(cwd: Path, tree: str, before: str = NO_GENERATE) -> None:
    """What a `verilator --json-only --dump-tree-json` run leaves in `cwd/obj_dir`."""
    out = cwd / "obj_dir"
    out.mkdir(exist_ok=True)
    (out / "Vtt_um_chip.tree.json").write_text(tree)
    (out / "Vtt_um_chip.tree.meta.json").write_text(TREE_META)
    (out / EARLY_STAGE).write_text(before)


def inputs(tmp_path: Path, netlist: str = "module tt_um_chip(); endmodule\n") -> tuple[Path, Path]:
    rtl = tmp_path / "rtl"
    rtl.mkdir()
    (rtl / "project.v").write_text("// wrapper\n")
    (rtl / "chip.v").write_text("// core\n")
    path = tmp_path / "netlist.v"
    path.write_text(netlist)
    return rtl, path


def go(tmp_path: Path, fake: FakeYosys, **kw: Any) -> dict[str, Any]:
    rtl, netlist = inputs(tmp_path, kw.pop("netlist", "module tt_um_chip(); endmodule\n"))
    got = equiv.check(rtl, "chip", netlist, tmp_path / "scratch", yosys=fake, **kw)
    assert got["ok"] is (got["verdict"] == "equivalent")  # only equivalent is ok
    assert got["verdict"] in ("equivalent", "not_equivalent", "not_proven", "error")
    if got["verdict"] == "not_proven":
        assert NOT_A_PASS in got["message"]
    if got["verdict"] != "equivalent":
        assert got["method"] is None
    return got


# -- equivalent ----------------------------------------------------------------------------------


def test_the_counter_is_proven_by_induction(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "equivalent" and got["ok"] is True and got["reason"] is None
    assert got["method"] == "equiv_induct" and got["first_mismatch"] is None
    assert got["stage1"]["proven"] == 24 and got["base"] == {"status": "ok"}
    assert got["cycles_covered"] == 32 and got["internal_inconsistency"] is False
    assert fake.tags == ["equiv", "base", "bmc_model", "bmc_once", "bmc"]
    assert set(got["seconds"]) == {"tree", "stage1", "base", "stage4", "total"}
    assert len(got["netlist_sha256"]) == 64
    assert "24 matched points proven by induction" in got["message"]
    assert "7 cycles" in got["message"]  # the base case covers seq + 2 cycles
    assert len(got["message"]) < 400


def test_every_call_runs_in_the_private_scratch_folder_with_its_own_time_limit(
    tmp_path: Path,
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    go(tmp_path, fake, budget_s=50.0, deadline_s=1000.0)
    assert all(t <= 50.0 for _, t in fake.calls)
    scratch = tmp_path / "scratch"
    assert {p.name for p in scratch.glob("*.ys")} == {
        "equiv.ys",
        "base.ys",
        "bmc_model.ys",
        "bmc_once.ys",
        "bmc.ys",
    }
    assert (scratch / "netlist.v").read_text() == "module tt_um_chip(); endmodule\n"
    assert (scratch / "project.v").is_file() and (scratch / "chip.v").is_file()
    assert (scratch / "base.il").is_file()
    # The scripts the check ran are the ones of equiv_script.
    assert "equiv_simple -seq 5" in (scratch / "equiv.ys").read_text()
    assert "read_verilog netlist.v" in (scratch / "equiv.ys").read_text()
    # The base case: RTL registers x, netlist flip-flops any defined value, nothing else fixed.
    base = (scratch / "base.ys").read_text()
    x_run = base.split("BASE-DEF")[0]
    assert "-set-all-undef-at 1 ff_gold -set-def-at 1 ff_gate" in x_run
    assert "-set-init" not in x_run and "-set-init-def" in base.split("BASE-DEF")[1]
    model = (scratch / "base.il").read_text()
    assert "\\ff_gold" in model and "\\ff_gate" in model
    # The referee: the miter as written, then the same power-up on the miter with its wires.
    assert "write_rtlil miter.il" in (scratch / "bmc_model.ys").read_text()
    assert "attrmap -remove init" in (scratch / "bmc_model.ys").read_text()  # no initial values
    bmc = (scratch / "bmc.ys").read_text()
    assert "read_rtlil miter_ff.il" in bmc and "-set-init" not in bmc
    assert "-set-all-undef-at 1 ff_gold -set-def-at 1 ff_gate" in bmc
    # The one-shot search first: the same constraints and cycles as one `sat` problem.
    once = (scratch / "bmc_once.ys").read_text()
    assert "read_rtlil miter_ff.il" in once and "-set-init" not in once
    assert "-set-all-undef-at 1 ff_gold -set-def-at 1 ff_gate" in once
    assert "-seq 33 -prove-skip 1" in once and "-tempinduct" not in once
    # the two searches run at once: each writes its own waveform file
    assert "-dump_vcd cex_bmc.vcd" in bmc and "cex_bmc_once" not in bmc
    assert "-dump_vcd cex_bmc_once.vcd" in once
    ff = (scratch / "miter_ff.il").read_text()
    assert "  wire width 8 \\ff_gold\n  connect \\ff_gold { \\gold.core.cnt }" in ff
    assert "  wire width 1 \\ff_gate\n  connect \\ff_gate { \\gate._1_.IQ }" in ff


def test_the_overall_deadline_caps_every_call(tmp_path: Path) -> None:
    fake = FakeYosys("counter_good", equiv=(None, "counter_good", "equiv.log"))
    got = go(tmp_path, fake, deadline_s=0.0)
    assert fake.calls[0][1] == 0.0
    assert got["verdict"] == "not_proven" and got["ok"] is False


def test_both_k_inductions_closing_is_equivalent(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "shiftnr_m0365", "equiv.log"),
        kind_x=(0, "shiftnr_m0365", "kind_x.log"),
        kind_zero=(0, "shiftnr_m0365", "kind_zero.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "equivalent" and got["ok"] is True
    assert got["method"] == "k-induction length 16" and fake.tags == [
        "equiv",
        "kind_x",
        "kind_zero",
    ]
    assert got["stage1"]["unproven"] >= 1  # stage 1 alone did not prove it


# -- not equivalent --------------------------------------------------------------------------------


def test_a_base_case_x_difference_on_an_output_is_not_equivalent(tmp_path: Path) -> None:
    """The X run gives every netlist flip-flop a defined power-up value, so a difference it finds
    on an output is real for that power-up state: `not_equivalent`, with no second run."""
    fake = FakeYosys(
        "counter_m0442",
        equiv=(0, "counter_m0442", "equiv.log"),
        base=(0, "counter_m0442", "base.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["ok"] is False
    assert fake.tags == ["equiv", "base"]  # no referee needed
    mismatch = got["first_mismatch"]
    assert mismatch["cycle"] == 1
    assert {"signal": "uio_oe[0]", "rtl": "0", "netlist": "1"} in mismatch["differs"]
    assert "cycle 1 (power-up, with rst_n low)" in got["message"]
    assert "uio_oe[0] (RTL 0, netlist 1)" in got["message"]
    assert "for some power-up state of its flip-flops" in got["message"]
    row = got["trace"][0]
    assert row["cycle"] == 1 and row["inputs"]["rst_n"] == "0"
    assert row["rtl"]["uio_oe[0]"] == "0" and row["netlist"]["uio_oe[0]"] == "1"
    assert all(k.startswith(("uo_out", "uio_out", "uio_oe")) for k in row["rtl"])  # pins only


@pytest.mark.parametrize(
    ("cell", "why"),
    [
        ("  cell $dlatch $d_gold\n    connect \\Q \\q_gold\n  end\n", "cannot set"),
        ("  cell $mem_v2 $m_gold\n  end\n", "cannot set"),
        ("  cell $_DFF_P_ $f\n    connect \\Q \\q\n  end\n", "cannot be read"),  # no side
        ("  cell $dff $f_gold\n    connect \\Q \\q_gold\n  end\n", "cannot be read"),  # width
        ("  attribute \\init 1'0\n  wire \\q_gold\n", "initial value"),
    ],
)
def test_a_state_whose_power_up_cannot_be_set_is_an_error_never_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cell: str, why: str
) -> None:
    il = (FIX / "counter_good" / "equiv.il").read_text()
    end = il.rstrip().rfind("\nend")
    bad = il[:end] + "\n" + cell + il[end + 1 :]
    real_copy = shutil.copy

    def copy(src: Path, dst: Path) -> Any:
        got = real_copy(src, dst)
        if Path(dst).name == "equiv.il":
            Path(dst).write_text(bad)
        return got

    monkeypatch.setattr(shutil, "copy", copy)
    fake = FakeYosys("counter_good", equiv=(0, "counter_good", "equiv.log"))
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and not got["ok"] and fake.tags == ["equiv"]
    assert why in got["message"]


@pytest.mark.parametrize("miter_il", [None, "module \\miter\nend\nmodule \\other\nend\n"])
def test_a_referee_model_that_cannot_be_read_is_an_error_never_a_pass(
    tmp_path: Path, miter_il: str | None
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
    )
    fake.miter_il = miter_il  # not written, or two modules
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and not got["ok"]
    assert fake.tags == ["equiv", "base", "bmc_model"]


def test_a_stale_referee_model_is_never_read(tmp_path: Path) -> None:
    """A `miter.il` left in the scratch folder by an earlier call is deleted first."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
    )
    fake.miter_il = None
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "miter.il").write_text(MITER_IL)
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and fake.tags == ["equiv", "base", "bmc_model"]


@pytest.mark.parametrize(("code", "verdict"), [(None, "not_proven"), (1, "error")])
def test_a_referee_model_call_that_fails_or_runs_out_of_time_is_never_a_pass(
    tmp_path: Path, code: int | None, verdict: str
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc_model=(code, "inline", "ERROR: boom\n"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == verdict and not got["ok"]
    assert fake.tags == ["equiv", "base", "bmc_model"]


def test_a_difference_found_from_reset_names_the_bit_the_cycle_and_the_trace(
    tmp_path: Path,
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),
        # its netlist bit is x (every flip-flop starts x): confirmed by the per-side search
        kind_x_check=(0, "counter_m0115", "kind_x.log"),
        kind_zero=None,
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["reason"] is None
    # the confirmed counterexample ended it
    assert fake.tags == [
        "equiv",
        "kind_x",
        "kind_x_check_model",
        "kind_x_check_once",
        "kind_x_check",
    ]
    assert "-set-init-undef" not in (tmp_path / "scratch" / "kind_x_check.ys").read_text()
    assert "read_rtlil miter_ff.il" in (tmp_path / "scratch" / "kind_x_check.ys").read_text()
    # the re-check reads the RTL as a chip powers up (initial values removed), the k-induction
    # the RTL as written
    noinit = equiv_script.stash(equiv_script.gold("chip", "rtl_noinit"), "chip", "gold")
    written = equiv_script.stash(equiv_script.gold("chip", "rtl"), "chip", "gold")
    assert (tmp_path / "scratch" / "kind_x_check_model.ys").read_text().startswith(noinit)
    assert (tmp_path / "scratch" / "kind_x.ys").read_text().startswith(written)
    mismatch = got["first_mismatch"]
    assert mismatch["cycle"] >= 2
    assert mismatch["differs"][0]["signal"].startswith(("uo_out[", "uio_out[", "uio_oe["))
    assert f"cycle {mismatch['cycle']}" in got["message"]
    trace = got["trace"]
    assert trace[-1]["cycle"] == mismatch["cycle"] and trace[0]["cycle"] == 1
    assert trace[0]["inputs"]["rst_n"] == "0" and set(trace[0]["inputs"]) == {
        "rst_n",
        "ui_in",
        "uio_in",
    }
    assert len(trace) <= equiv.MAX_EQUIV_TRACE


def test_an_x_only_difference_with_every_flip_flop_at_x_must_be_confirmed(tmp_path: Path) -> None:
    """Round 13 n23v: with every flip-flop x, a mux2 whose inputs are equal gives x for an x
    select, where every real power-up state gives the same value. The per-side search (netlist
    flip-flops at any defined value) finds no difference: not proven, never not equivalent."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),  # uo_out[5]: RTL 0, netlist x
        kind_x_check=(0, "counter_good", "bmc.log"),  # the per-side search: no difference
        kind_zero=(0, "counter_m0043", "kind_zero.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "unproven_points"
    assert fake.tags == [
        "equiv",
        "kind_x",
        "kind_x_check_model",
        "kind_x_check_once",
        "kind_x_check",
        "kind_zero",
    ]
    assert got["first_mismatch"] is None and got["cycles_covered"] == 32
    assert "first 32 cycles from reset" in got["message"]


# -- the one-shot search from reset (stage 4) ------------------------------------------------------

PROVEN = {
    "equiv": (0, "counter_good", "equiv.log"),
    "base": (0, "counter_good", "base.log"),
}


def test_a_one_shot_proof_ends_the_check_from_reset(tmp_path: Path) -> None:
    """Round 14 v62: a registered seven-segment decoder. The step-by-step search stalls after 19
    cycles; the one-shot search proves all 32 in a second, which decides the race: the
    step-by-step search is stopped (with this fake, which runs the two in turn, never started)."""
    fake = FakeYosys("counter_good", **PROVEN, bmc_once=(0, "seg7reg", "bmc_once.log"))
    got = go(tmp_path, fake)
    assert got["verdict"] == "equivalent" and got["ok"] is True and got["reason"] is None
    assert fake.tags == ["equiv", "base", "bmc_model", "bmc_once"]
    assert got["cycles_covered"] == 32 and got["time_limit_hit"] is False
    assert "found no difference in 32 cycles" in got["message"]


def test_a_one_shot_difference_falls_back_to_the_step_by_step_first_difference(
    tmp_path: Path,
) -> None:
    """counter_swap: one cell swapped. The one-shot finds a difference; the step-by-step search
    then reports it at its first cycle with its own trace (the one-shot trace is not used)."""
    fake = FakeYosys(
        "counter_good",
        **PROVEN,
        bmc_once=(0, "counter_swap", "bmc_once.log"),
        bmc=(0, "counter_swap", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["ok"] is False
    assert fake.tags == ["equiv", "base", "bmc_model", "bmc_once", "bmc"]
    step = equiv_parse.parse_cex((FIX / "counter_swap" / "bmc.log").read_text())
    assert got["first_mismatch"] == step["first_mismatch"]
    assert got["trace"] == equiv._trace(step["trace"], step["first_mismatch"])
    assert "At cycle 4" in got["message"] and "uo_out[1]" in got["message"]


@pytest.mark.parametrize("bmc", [(None, "inline", ""), (0, "counter_good", "bmc.log")])
def test_a_one_shot_difference_the_step_by_step_search_does_not_find_is_reported(
    tmp_path: Path, bmc: tuple[int | None, str, str]
) -> None:
    """The step-by-step search runs out of time (or, against the one-shot, finds nothing in 32
    cycles): the one-shot's own model is the counterexample, never a pass."""
    fake = FakeYosys(
        "counter_good", **PROVEN, bmc_once=(0, "counter_swap", "bmc_once.log"), bmc=bmc
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["ok"] is False
    assert fake.tags == ["equiv", "base", "bmc_model", "bmc_once", "bmc"]
    once = equiv_parse.parse_cex((FIX / "counter_swap" / "bmc_once.log").read_text())
    assert got["first_mismatch"] == once["first_mismatch"]
    assert got["trace"] == equiv._trace(once["trace"], once["first_mismatch"])
    assert got["trace"][-1]["cycle"] == 4


def test_a_one_shot_difference_whose_bits_cannot_be_read_is_not_proven(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        **PROVEN,
        bmc_once=(0, "inline", "SAT proof finished - model found: FAIL!\n"),
        bmc=(None, "inline", ""),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "unreadable_counterexample"


def test_a_one_shot_time_out_falls_back_to_the_step_by_step_search(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        **PROVEN,
        bmc_once=(None, "seg7reg", "bmc_once.log"),  # stopped, whatever it printed
        bmc=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "equivalent" and got["cycles_covered"] == 32
    assert fake.tags == ["equiv", "base", "bmc_model", "bmc_once", "bmc"]


def test_both_searches_out_of_time_is_not_proven(tmp_path: Path) -> None:
    """v62 before round 14: the step-by-step search stopped after 18 of 32 cycles."""
    fake = FakeYosys(
        "counter_good", **PROVEN, bmc_once=(None, "inline", ""), bmc=(None, "seg7reg", "bmc.log")
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert got["cycles_covered"] == 18 and "after 18 of 32 cycles" in got["message"]
    assert f"cycles ({equiv.SOLVER_HINT}). {NOT_A_PASS}" in got["message"]


def without(scenario: str, name: str, line: str) -> str:
    text = (FIX / scenario / name).read_text()
    kept = [x for x in text.splitlines() if x != line]
    assert len(kept) < len(text.splitlines()), line
    return "\n".join(kept) + "\n"


@pytest.mark.parametrize(
    "once",
    [
        (0, "inline", "SAT proof finished - no model found: SUCCESS!\n"),  # nothing else
        (0, "inline", ""),
        (1, "seg7reg", "bmc_once.log"),  # a clean log, but a failing exit code
        (0, "inline", (FIX / "seg7reg" / "bmc_once.log").read_text() * 2),  # two end lines
        # the power-up constraints, the reset or the last cycle missing from the log
        (0, "inline", without("seg7reg", "bmc_once.log", "Import def constraint for this "
                              "timestep: \\ff_gate")),
        (0, "inline", without("seg7reg", "bmc_once.log", "Import all_undef constraint for "
                              "this timestep: \\ff_gold")),
        (0, "inline", without("seg7reg", "bmc_once.log", "Import set-constraint for this "
                              "timestep: \\in_rst_n = 1'0")),
        (0, "inline", without("seg7reg", "bmc_once.log", "Setting up time step 33:")),
    ],
)  # fmt: skip
def test_an_unclear_one_shot_log_falls_back_to_the_step_by_step_search(
    tmp_path: Path, once: tuple[int | None, str, str]
) -> None:
    fake = FakeYosys("counter_good", **PROVEN, bmc_once=once, bmc=(None, "seg7reg", "bmc.log"))
    got = go(tmp_path, fake)
    assert fake.tags == ["equiv", "base", "bmc_model", "bmc_once", "bmc"]
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"  # never a pass


@pytest.mark.parametrize(("budget", "deadline"), [(60.0, 1000.0), (120.0, 1000.0), (120.0, 100.0)])
def test_both_searches_from_reset_get_the_whole_time_of_one_call(
    tmp_path: Path, budget: float, deadline: float
) -> None:
    """The two searches race (round 16): each gets what one call may take, no cap, no share."""
    assert not hasattr(equiv, "ONCE_S") and not hasattr(equiv.Clock, "first_try")
    fake = FakeYosys("counter_good", **PROVEN, bmc=(0, "counter_good", "bmc.log"))
    got = go(tmp_path, fake, budget_s=budget, deadline_s=deadline)
    timeouts = dict(fake.calls)
    assert timeouts["bmc_once"] == timeouts["bmc"]  # the same limit, set when the race starts
    assert min(budget, deadline) - 2.0 < timeouts["bmc"] <= budget
    assert got["verdict"] == "equivalent"


# -- the race of the two searches from reset (round 16) -------------------------------------------

PROVED = {"status": "no_cex", "covered": 32}
STOPPED = {"status": "no_cex", "covered": 18, "stopped": "time limit"}
FAILED = {"status": "fail", "failure": {"verdict": "error", "reason": "yosys_failed"}}
STEP_CEX = {"status": "cex", "first_mismatch": {"cycle": 4, "differs": []}, "trace": []}
ONCE_CEX_LOG = (FIX / "counter_swap" / "bmc_once.log").read_text()


@pytest.mark.parametrize(
    ("once", "step", "want"),
    [
        # the one-shot proved every cycle: proved, whatever the other search is doing
        ("proved", None, "proved"),
        ("proved", STOPPED, "proved"),
        ("proved", FAILED, "proved"),
        # the step-by-step search found a difference: that one, at its first cycle
        (None, STEP_CEX, "step"),
        ("cex", STEP_CEX, "step"),
        ("unclear", STEP_CEX, "step"),
        # it reached the depth: proved, unless the one-shot found a difference
        (None, PROVED, "step"),
        ("unclear", PROVED, "step"),
        ("cex", PROVED, "once"),  # it contradicts the one-shot: the one-shot's model
        # a one-shot difference: wait for the first cycle; a time-out or a failure reports it
        ("cex", None, "wait"),
        ("cex", STOPPED, "once"),
        ("cex", FAILED, "once"),
        # the step-by-step search stopped or failed: wait for the one-shot, else its own result
        (None, STOPPED, "wait"),
        (None, FAILED, "wait"),
        ("unclear", STOPPED, "step"),
        ("unclear", FAILED, "step"),
        # an unclear one-shot (time-out, error, a process that died): the other search decides
        ("unclear", None, "wait"),
        (None, None, "wait"),
    ],
)
def test_the_race_decides_on_every_combination_and_never_passes_by_default(
    once: str | None, step: dict[str, Any] | None, want: str
) -> None:
    got = equiv._race_decision(once, ONCE_CEX_LOG, step, 32)
    if want == "wait":
        assert got is None
    elif want == "proved":
        assert got == {"status": "no_cex", "covered": 32}
    elif want == "step":
        assert got is step
    else:
        assert got == {"status": "cex", **equiv_parse.parse_cex(ONCE_CEX_LOG)}
        assert got["first_mismatch"]["differs"]


class OrderedRace:
    """A race whose scripts end in the order `order` (tags), answered by a FakeYosys. `killed`:
    the scripts still running when the check closed the race."""

    def __init__(self, fake: FakeYosys, order: list[str]) -> None:
        self.fake, self.order = fake, order
        self.started: list[str] = []
        self.killed: list[str] | None = None

    def __call__(self, cwd: Path, jobs: list[tuple[str, str]], timeout: float) -> Any:
        scripts = {tag: script for script, tag in jobs}
        self.started = [tag for _, tag in jobs]
        pending = list(self.order)
        try:
            while pending:
                tag = pending[0]
                code, log = self.fake(cwd, scripts[tag], tag, timeout)
                pending.pop(0)
                yield tag, code, log
        finally:
            self.killed = pending


def test_a_step_by_step_difference_ends_the_race_and_kills_the_one_shot(tmp_path: Path) -> None:
    fake = FakeYosys("counter_good", **PROVEN, bmc=(0, "counter_swap", "bmc.log"))
    race = OrderedRace(fake, ["bmc", "bmc_once"])
    got = go(tmp_path, fake, race=race)
    assert race.started == ["bmc_once", "bmc"] and race.killed == ["bmc_once"]
    assert got["verdict"] == "not_equivalent" and got["first_mismatch"]["cycle"] == 4
    step = equiv_parse.parse_cex((FIX / "counter_swap" / "bmc.log").read_text())
    assert got["trace"] == equiv._trace(step["trace"], step["first_mismatch"])


def test_a_step_by_step_search_that_reaches_the_depth_first_proves_it(tmp_path: Path) -> None:
    fake = FakeYosys("counter_good", **PROVEN, bmc=(0, "counter_good", "bmc.log"))
    race = OrderedRace(fake, ["bmc", "bmc_once"])
    got = go(tmp_path, fake, race=race)
    assert race.killed == ["bmc_once"]
    assert got["verdict"] == "equivalent" and got["cycles_covered"] == 32


def test_a_one_shot_proof_kills_the_step_by_step_search(tmp_path: Path) -> None:
    fake = FakeYosys("counter_good", **PROVEN, bmc_once=(0, "seg7reg", "bmc_once.log"))
    race = OrderedRace(fake, ["bmc_once", "bmc"])
    got = go(tmp_path, fake, race=race)
    assert race.killed == ["bmc"] and "bmc" not in fake.tags
    assert got["verdict"] == "equivalent" and got["cycles_covered"] == 32


@pytest.mark.parametrize(
    "bmc", [(None, "seg7reg", "bmc.log"), (1, "inline", "ERROR: boom\n"), (-9, "inline", "")]
)
def test_a_one_shot_proof_after_the_step_by_step_search_gave_up_still_proves_it(
    tmp_path: Path, bmc: tuple[int | None, str, str]
) -> None:
    """The step-by-step search stopped at its limit, failed, or was killed (OOM): the one-shot,
    still running, may prove every cycle."""
    fake = FakeYosys("counter_good", **PROVEN, bmc=bmc, bmc_once=(0, "seg7reg", "bmc_once.log"))
    race = OrderedRace(fake, ["bmc", "bmc_once"])
    got = go(tmp_path, fake, race=race)
    assert race.killed == []
    assert got["verdict"] == "equivalent" and got["cycles_covered"] == 32


@pytest.mark.parametrize(
    ("bmc", "verdict", "reason"),
    [
        ((None, "seg7reg", "bmc.log"), "not_proven", "time_limit"),
        ((1, "inline", "ERROR: boom\n"), "error", "yosys_failed"),
        ((-9, "inline", ""), "error", "yosys_failed"),  # killed (OOM): never a pass
    ],
)
@pytest.mark.parametrize("once", [(None, "inline", ""), (-9, "seg7reg", "bmc_once.log")])
def test_when_neither_search_decides_the_step_by_step_result_stands(
    tmp_path: Path,
    once: tuple[int | None, str, str],
    bmc: tuple[int | None, str, str],
    verdict: str,
    reason: str,
) -> None:
    """The one-shot timed out or died (a killed process whose log shows a whole proof is still
    unclear); the step-by-step search timed out, failed or died: never a pass."""
    for order in (["bmc", "bmc_once"], ["bmc_once", "bmc"]):
        fake = FakeYosys("counter_good", **PROVEN, bmc=bmc, bmc_once=once)
        (tmp_path / order[0]).mkdir()
        got = go(tmp_path / order[0], fake, race=OrderedRace(fake, order))
        assert (got["verdict"], got["reason"]) == (verdict, reason), order
        assert got["ok"] is False


def test_a_one_shot_difference_waits_for_the_first_differing_cycle(tmp_path: Path) -> None:
    """The one-shot ends first with a difference: the step-by-step search runs on and reports
    it at its first cycle (not the one-shot's trace)."""
    fake = FakeYosys(
        "counter_good",
        **PROVEN,
        bmc_once=(0, "counter_swap", "bmc_once.log"),
        bmc=(0, "counter_swap", "bmc.log"),
    )
    race = OrderedRace(fake, ["bmc_once", "bmc"])
    got = go(tmp_path, fake, race=race)
    assert race.killed == []
    step = equiv_parse.parse_cex((FIX / "counter_swap" / "bmc.log").read_text())
    assert got["verdict"] == "not_equivalent" and got["first_mismatch"] == step["first_mismatch"]


# A stand-in for Yosys in the real race (`race_yosys`): `python helper.py <plan.json> <tag>` in
# the scratch folder. The plan says, per tag, what to write to `<tag>.log`, how long to sleep
# first, how many bytes to write to its standard output, whether to start a child that sleeps
# (to see that the whole group is killed), whether to kill itself (as the OOM killer would),
# and its exit code. It writes its pid (and its child's) to `<tag>.pid`, all at once. With
# `"after": [file, n]` it first waits (up to 20 s) until `file` holds n pids, so that it ends
# only after another helper has started.
HELPER = """
import json, os, signal, subprocess, sys, time
plan = json.load(open(sys.argv[1]))[sys.argv[2]]
tag = sys.argv[2]
pids = [os.getpid()]
if plan.get("child"):
    pids.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]).pid)
open(f"{tag}.tmp", "w").write(" ".join(map(str, pids)))
os.replace(f"{tag}.tmp", f"{tag}.pid")
if plan.get("after"):
    name, count = plan["after"]
    for _ in range(400):
        if os.path.exists(name) and len(open(name).read().split()) >= count:
            break
        time.sleep(0.05)
time.sleep(plan.get("sleep", 0))
sys.stdout.write("x" * plan.get("stdout", 0))
sys.stdout.flush()
open(f"{tag}.log", "w").write(plan.get("log", ""))
sys.stderr.write(plan.get("stderr", ""))
if plan.get("kill"):
    os.kill(os.getpid(), signal.SIGKILL)
sys.exit(plan.get("code", 0))
"""


def helper_race(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plan: dict[str, dict[str, Any]]
) -> None:
    helper, plan_file = tmp_path / "helper.py", tmp_path / "plan.json"
    helper.write_text(HELPER)
    plan_file.write_text(json.dumps(plan))
    monkeypatch.setattr(
        equiv, "_yosys_argv", lambda tag: [sys.executable, str(helper), str(plan_file), tag]
    )


def gone(pid: int) -> bool:
    """True once the process has ended (no such process, or a zombie no one reaped yet)."""
    for _ in range(100):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except OSError:
            return True
        if state in ("Z", "X"):
            return True
        time.sleep(0.05)
    return False


def test_the_real_race_reports_each_script_as_it_ends(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper_race(
        monkeypatch,
        tmp_path,
        {
            "slow": {"sleep": 1.0, "log": "slow log\n", "stderr": "warn\n", "code": 0},
            "fast": {"log": "fast log\n", "code": 3},
        },
    )
    started = time.monotonic()
    got = list(equiv.race_yosys(tmp_path, [("script a", "slow"), ("script b", "fast")], 30.0))
    assert got == [("fast", 3, "fast log\n"), ("slow", 0, "slow log\nwarn\n")]
    assert time.monotonic() - started < 10.0  # at once, not one after the other
    assert (tmp_path / "slow.ys").read_text() == "script a"  # each with its own script and log


def test_a_script_that_writes_a_lot_to_its_output_still_ends_in_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """RY3 (round 19): no one reads a script's standard output, so it must not be a pipe: a
    Yosys that printed more than a pipe holds would block until the time limit (a false
    `time_limit`) instead of ending with its exit code."""
    helper_race(monkeypatch, tmp_path, {"loud": {"stdout": 2_000_000, "log": "ok\n", "code": 0}})
    started = time.monotonic()
    got = list(equiv.race_yosys(tmp_path, [("", "loud")], 30.0))
    assert got == [("loud", 0, "ok\n")]
    assert time.monotonic() - started < 15.0


def test_closing_the_race_kills_the_other_search_and_its_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper_race(
        monkeypatch,
        tmp_path,
        {"fast": {"log": "done\n"}, "slow": {"sleep": 60, "child": True}},
    )
    started = time.monotonic()
    race = equiv.race_yosys(tmp_path, [("", "slow"), ("", "fast")], 60.0)
    assert next(race) == ("fast", 0, "done\n")
    for _ in range(100):  # the slow one has written its pids
        if (tmp_path / "slow.pid").exists() and len(
            (tmp_path / "slow.pid").read_text().split()
        ) == 2:
            break
        time.sleep(0.05)
    pids = [int(x) for x in (tmp_path / "slow.pid").read_text().split()]
    race.close()
    # the script itself is reaped by the time close() returns: this process started it, so
    # without a wait it would stay a zombie (which `gone` counts as ended)
    assert not Path(f"/proc/{pids[0]}").exists(), pids
    assert all(gone(pid) for pid in pids), pids  # the script and the child it started
    assert time.monotonic() - started < 20.0


def test_a_script_at_the_time_limit_is_killed_and_reported_as_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper_race(monkeypatch, tmp_path, {"slow": {"sleep": 60, "child": True}})
    got = list(equiv.race_yosys(tmp_path, [("", "slow")], 1.0))
    assert got == [("slow", None, "")]
    assert all(gone(int(pid)) for pid in (tmp_path / "slow.pid").read_text().split())


def test_one_yosys_at_its_time_limit_is_killed_with_its_children_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`run_yosys` stops a script at the limit with its whole process group (the real `yosys`
    is a wrapper that starts the solver), so no container-wide reap is needed; a script that
    ends in time is read as before."""
    monkeypatch.setattr(equiv.emit, "_reap_processes", lambda: pytest.fail("reaped the container"))
    helper_race(
        monkeypatch,
        tmp_path,
        {"slow": {"sleep": 60, "child": True}, "fast": {"log": "ok\n", "stderr": "w\n"}},
    )
    started = time.monotonic()
    assert equiv.run_yosys(tmp_path, "script", "slow", 1.0) == (None, "")
    assert time.monotonic() - started < 20.0
    assert all(gone(int(pid)) for pid in (tmp_path / "slow.pid").read_text().split())
    assert equiv.run_yosys(tmp_path, "script", "fast", 30.0) == (0, "ok\nw\n")
    assert (tmp_path / "fast.ys").read_text() == "script"
    assert equiv.run_yosys(tmp_path, "script", "none", 0.0) == (None, "")


def test_a_log_left_by_an_earlier_run_is_never_read_as_this_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A script stopped at the limit before it wrote its log: an old `<tag>.log` (here a whole
    proof) is removed before the race starts, so the race reports an empty log."""
    helper_race(monkeypatch, tmp_path, {"slow": {"sleep": 60}})
    (tmp_path / "slow.log").write_text(ONCE_PROOF_LOG)
    got = list(equiv.race_yosys(tmp_path, [("", "slow")], 1.0))
    assert got == [("slow", None, "")]


def test_the_real_yosys_runner_races_and_any_other_runs_in_turn(tmp_path: Path) -> None:
    clock = equiv.Clock(10.0, 10.0)
    assert equiv._Run(tmp_path, equiv.run_yosys, clock, 0.0).race is equiv.race_yosys
    fake = FakeYosys("counter_good")
    assert equiv._Run(tmp_path, fake, clock, 0.0).race is not equiv.race_yosys


def test_a_race_with_no_time_left_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    helper_race(monkeypatch, tmp_path, {"a": {}, "b": {}})
    got = list(equiv.race_yosys(tmp_path, [("", "a"), ("", "b")], 0.0))
    assert got == [("a", None, ""), ("b", None, "")]
    assert not (tmp_path / "a.pid").exists()


ONCE_PROOF_LOG = (FIX / "seg7reg" / "bmc_once.log").read_text()


def real_race_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plan: dict[str, dict[str, Any]], **kw: Any
) -> dict[str, Any]:
    """The whole check with the real race of real processes for stage 4 (FakeYosys before)."""
    (tmp_path / "s").mkdir()
    helper_race(monkeypatch, tmp_path / "s", plan)
    fake = FakeYosys("counter_good", **PROVEN)
    return go(tmp_path, fake, race=equiv.race_yosys, **kw)


def test_the_real_race_ends_with_a_one_shot_proof_and_kills_the_slow_search(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    started = time.monotonic()
    got = real_race_check(
        monkeypatch,
        tmp_path,
        {  # the one-shot ends only once the slow search has started its child
            "bmc_once": {"log": ONCE_PROOF_LOG, "after": ["bmc.pid", 2]},
            "bmc": {"sleep": 60, "child": True},
        },
    )
    assert got["verdict"] == "equivalent" and got["cycles_covered"] == 32
    assert time.monotonic() - started < 20.0
    pids = (tmp_path / "scratch" / "bmc.pid").read_text().split()
    assert all(gone(int(pid)) for pid in pids)


def test_a_one_shot_killed_by_a_signal_is_never_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A one-shot that printed a whole proof but was then killed (the OOM killer, a signal) is
    unclear; the step-by-step search times out: not proven."""
    got = real_race_check(
        monkeypatch,
        tmp_path,
        {
            "bmc_once": {"log": ONCE_PROOF_LOG, "kill": True},
            "bmc": {"sleep": 60, "log": (FIX / "seg7reg" / "bmc.log").read_text()},
        },
        budget_s=2.0,
    )
    assert (got["verdict"], got["reason"]) == ("not_proven", "time_limit")


def test_a_step_by_step_search_killed_by_a_signal_is_never_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The step-by-step search printed its whole proof and was then killed: not a proof."""
    got = real_race_check(
        monkeypatch,
        tmp_path,
        {
            "bmc_once": {"sleep": 60},
            "bmc": {"log": (FIX / "counter_good" / "bmc.log").read_text(), "kill": True},
        },
        budget_s=2.0,
    )
    assert got["verdict"] == "error" and got["reason"] == "yosys_failed" and got["ok"] is False


def test_the_x_only_recheck_also_tries_the_one_shot_first(tmp_path: Path) -> None:
    """kind_x finds a difference whose netlist bits are all x; the per-side re-check proves it
    away with the one-shot (no step-by-step re-check), and the zero-start k-induction decides."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),  # uo_out[5]: RTL 0, netlist x
        kind_x_check_once=(0, "seg7reg", "bmc_once.log"),
        kind_zero=(0, "counter_m0043", "kind_zero.log"),
    )
    got = go(tmp_path, fake)
    assert fake.tags == ["equiv", "kind_x", "kind_x_check_model", "kind_x_check_once", "kind_zero"]
    assert got["verdict"] == "not_proven" and got["reason"] == "unproven_points"
    assert got["first_mismatch"] is None and got["cycles_covered"] == 32


def test_the_x_only_recheck_reports_a_one_shot_difference_at_the_first_cycle(
    tmp_path: Path,
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),
        kind_x_check_once=(0, "counter_swap", "bmc_once.log"),
        kind_x_check=(0, "counter_swap", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert fake.tags[-2:] == ["kind_x_check_once", "kind_x_check"]
    assert got["verdict"] == "not_equivalent" and got["first_mismatch"]["cycle"] == 4
    step = equiv_parse.parse_cex((FIX / "counter_swap" / "bmc.log").read_text())
    assert got["trace"] == equiv._trace(step["trace"], step["first_mismatch"])


def test_the_referee_finding_a_difference_after_a_clean_base_case_is_never_a_pass(
    tmp_path: Path,
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "counter_m0115", "kind_x.log"),  # a real counterexample from reset
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["ok"] is False
    assert got["internal_inconsistency"] is True and got["reason"] == "internal_inconsistency"
    assert got["first_mismatch"]["cycle"] >= 2 and got["trace"]


def initz_stage1_for_counter() -> str:
    """The counter's stage-1 log with an initial-value block (the RTL "has initial values")."""
    log = (FIX / "counter_good" / "equiv.log").read_text()
    return log.replace("INITS-BEGIN\nINITS-END", "INITS-BEGIN\ntt_um_chip/core.cnt\nINITS-END")


def test_the_rtl_relying_on_initial_values_is_not_equivalent(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "initz", "equiv.log"),
        initstate=(0, "initstate_initz", "initstate.log"),  # the state differs after reset
        initdep=(0, "initz", "initdep.log"),  # and the search from reset sees it on the pins
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["reason"] == "rtl_initial_value"
    assert fake.tags == ["equiv", "initstate", "initdep"]
    assert "Reset those registers with rst_n" in got["message"]
    assert "initial values" in got["message"] and got["first_mismatch"]["cycle"] >= 1


def test_initial_values_that_reset_overwrites_let_the_check_go_on(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "inline", initz_stage1_for_counter()),
        initstate=(0, "initstate_equal", "initstate.log"),  # equal state after the reset cycle
        base=(0, "counter_good", "base.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "equivalent"
    assert fake.tags == ["equiv", "initstate", "base", "bmc_model", "bmc_once", "bmc"]
    script = (tmp_path / "scratch" / "initstate.ys").read_text()
    assert "expose -dff" in script and "-prove-skip 1" in script  # the state at cycle 2


def test_an_initial_value_that_only_shows_late_is_not_proven_never_a_pass(
    tmp_path: Path,
) -> None:
    """The state check sees a register that keeps its initial value, but the bounded search from
    reset finds no output difference in its depth (the value reaches a pin only later). The old
    check called this equivalent; it must be not proven and name the register."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "inline", initz_stage1_for_counter()),
        initstate=(0, "initstate_deep", "initstate.log"),
        initdep=(0, "counter_good", "bmc.log"),  # a search with no counterexample, 32 cycles
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "initial_values"
    assert fake.tags == ["equiv", "initstate", "initdep"]  # no base case, no referee after it
    assert "core.flag" in got["message"] and "rst_n" in got["message"]
    assert got["cycles_covered"] == 32 and got["time_limit_hit"] is False


def test_a_register_the_state_check_cannot_see_has_its_own_reason(tmp_path: Path) -> None:
    """GUARD_EXPOSE failed (a flip-flop output could not be exposed): the initial-value check
    cannot vouch for that register. Not the generic initial-value text, and never a pass."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "inline", initz_stage1_for_counter()),
        initstate=(1, "expose_fail", "initstate.log"),  # a real log of the failing guard
        initdep=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "state_check_unusable"
    assert "cannot look at" in got["message"] and "initial values" in got["message"]
    assert got["cycles_covered"] == 32 and fake.tags == ["equiv", "initstate", "initdep"]


@pytest.mark.parametrize("memory", [True, False])
def test_the_memory_hint_is_only_given_when_the_design_has_a_memory(
    tmp_path: Path, memory: bool
) -> None:
    log = initz_stage1_for_counter()
    if memory:  # the stage-1 script lists the RTL's memories between these markers
        log = log.replace("MEMS-BEGIN\nMEMS-END", "MEMS-BEGIN\ntt_um_chip/mem\nMEMS-END")
        if "MEMS-BEGIN" not in log:
            log = "MEMS-BEGIN\ntt_um_chip/mem\nMEMS-END\n" + log
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "inline", log),
        initstate=(0, "initstate_deep", "initstate.log"),
        initdep=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert ("memory's contents cannot be set by rst_n" in got["message"]) is memory


def test_an_initial_value_check_that_cannot_be_read_is_not_a_pass(tmp_path: Path) -> None:
    """A state check that prints nothing is treated as a difference, then the bounded search
    decides: a clean search is not proven."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "inline", initz_stage1_for_counter()),
        initstate=(0, "inline", ""),
        initdep=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "initial_values"


def test_an_initial_value_check_that_runs_out_of_time_is_not_proven(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "inline", initz_stage1_for_counter()),
        initstate=(None, "inline", ""),
        initdep=(None, "inline", ""),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert got["time_limit_hit"] is True and "ran out of time" in got["message"]


def test_the_independent_check_from_reset_running_out_of_time_is_not_proven(
    tmp_path: Path,
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(None, "inline", ""),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert got["time_limit_hit"] is True and got["ok"] is False
    assert got["message"] == (
        "Could not prove equivalence: the induction proof closed, but the independent check "
        "from reset ran out of time after 0 of 32 cycles (arithmetic such as multipliers, "
        "multiply-accumulate, or CRC/XOR chains mixed with adders is hard for the solver; a "
        "smaller width or splitting the logic can help). This is NOT a pass."
    )


def test_a_base_case_that_runs_out_of_time_is_not_proven_and_says_why(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good", equiv=(0, "counter_good", "equiv.log"), base=(None, "inline", "")
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert got["time_limit_hit"] is True and got["ok"] is False
    assert fake.tags == ["equiv", "base"]  # no check from reset after it
    assert got["message"] == (
        "Could not check the base case: a Yosys step ran out of time (arithmetic such as "
        "multipliers, multiply-accumulate, or CRC/XOR chains mixed with adders is hard for the "
        "solver; a smaller width or splitting the logic can help). This is NOT a pass."
    )


@pytest.mark.parametrize("bmc_log", ["", "SAT temporal induction proof finished\n", "Reached"])
def test_a_cut_off_check_from_reset_with_exit_code_0_is_an_error(
    tmp_path: Path, bmc_log: str
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "inline", bmc_log),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and got["reason"] == "yosys_failed"


@pytest.mark.parametrize(
    "base_log",
    [
        "",
        "BASE-X\nSAT proof finished - no model found: SUCCESS!\n",  # the DEF run is missing
        "BASE-X\nsomething else\nBASE-DEF\nSAT proof finished - no model found: SUCCESS!\n",
    ],
)
def test_an_empty_or_cut_off_base_case_log_with_exit_code_0_is_an_error(
    tmp_path: Path, base_log: str
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "inline", base_log),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and got["reason"] == "yosys_failed"
    assert fake.tags == ["equiv", "base"]


def test_a_counterexample_whose_bits_cannot_be_read_is_not_proven_and_does_not_crash(
    tmp_path: Path,
) -> None:
    log = (
        (FIX / "counter_good" / "bmc.log")
        .read_text()
        .replace(
            "Reached maximum number of time steps -> proved base case for 32 steps: SUCCESS!",
            "SAT temporal induction proof finished - model found for base case: FAIL!",
        )
    )
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "inline", log),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "unreadable_counterexample"
    assert "could not be read" in got["message"]


@pytest.mark.parametrize("const", ["1'z", "8'0000000z", "1'x", "4'01m0", "1'-"])
def test_an_x_or_z_constant_the_text_guard_missed_is_an_error_not_a_pass(
    tmp_path: Path, const: str
) -> None:
    """Yosys's own reading of the netlist is checked too: whatever the spelling was, a z left in
    it would be read by the proof as a plain 0."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    fake.gate_read = f"module \\tt_um_chip\n  connect \\a {const}\nend\n"
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and got["reason"] == "xz_constant"
    assert "unknown constant" in got["message"] and fake.tags == ["equiv"]


def test_a_missing_gate_reading_is_an_error_not_a_pass(tmp_path: Path) -> None:
    fake = FakeYosys("counter_good", equiv=(0, "counter_good", "equiv.log"))
    fake.gate_read = ""  # written empty: nothing to look at is not "nothing found"
    # An empty file has no constant, but a file that does not exist must not be read as clean:
    original = fake.__call__

    def without_file(cwd: Path, script: str, tag: str, timeout: float) -> tuple[int | None, str]:
        out = original(cwd, script, tag, timeout)
        (cwd / "gate_read.il").unlink(missing_ok=True)
        return out

    got = go(tmp_path, without_file)  # type: ignore[arg-type]
    assert got["verdict"] == "error" and got["reason"] == "xz_constant"


# -- not proven: never a pass ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scenario", "reason", "word"),
    [
        ("clkinv", "guard_clocking", "falling-edge"),
        ("tristz", "guard_tristate", "uio_oe"),
        ("async_m0229", "guard_async_set_reset", "dfbb"),
    ],
)
def test_a_guard_refusal_is_not_proven(
    tmp_path: Path, scenario: str, reason: str, word: str
) -> None:
    got = go(tmp_path, FakeYosys("counter_good", equiv=(1, scenario, "equiv.log")))
    assert got["verdict"] == "not_proven" and got["ok"] is False and got["reason"] == reason
    assert word in got["message"] and got["stage1"] == {"status": "fail"}
    assert got["base"] is None and got["first_mismatch"] is None


@pytest.mark.parametrize("which", ["project.v", "chip.v"])
@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("`ifdef SYNTHESIS\n`endif\n", "macro or directive `ifdef"),
        ("`ifdef __ICARUS__\n`endif\n", "macro or directive `ifdef"),
        ("`ifndef VERILATOR\n`endif\n", "macro or directive `ifndef"),
        ("bit b;\n", "two-state type `bit`"),
        ("assign #1 y = a;\n", "delay"),
        ("unique case (a)\n", "`unique`"),
        ('initial $readmemh("f.hex", m);\n', "$readmemh"),
    ],
)
def test_rtl_that_reads_differently_in_simulation_and_synthesis_is_not_proven(
    tmp_path: Path, which: str, text: str, word: str
) -> None:
    rtl, netlist = inputs(tmp_path)
    (rtl / which).write_text(text)
    fake = FakeYosys("")  # no Yosys call is expected: the text guard runs first
    got = equiv.check(rtl, "chip", netlist, tmp_path / "scratch", yosys=fake)
    assert got["verdict"] == "not_proven" and got["ok"] is False
    assert got["reason"] == "guard_sim_synth_differ" and fake.calls == []
    assert which in got["message"] and NOT_A_PASS in got["message"] and word in got["message"]
    assert "line " in got["message"]


def test_the_proof_reads_the_cleaned_files_and_the_originals_stay_untouched(
    tmp_path: Path,
) -> None:
    rtl, netlist = inputs(tmp_path)
    original = "// synthesis translate_off\n(* nosync *) reg r;\n// synthesis translate_on\n"
    (rtl / "chip.v").write_text(original)
    fake = FakeYosys("counter_good", equiv=(1, "counter_good", "equiv.log"))
    equiv.check(rtl, "chip", netlist, tmp_path / "scratch", yosys=fake)
    seen = (tmp_path / "scratch" / "chip.v").read_text()
    assert "translate_off" not in seen and "nosync" not in seen and "reg r;" in seen
    assert seen.count("\n") == original.count("\n")  # line numbers do not move
    assert (rtl / "chip.v").read_text() == original


def test_the_proof_reads_bytes_not_decoded_text(tmp_path: Path) -> None:
    """Comments may hold any bytes (even invalid UTF-8): they are blanked. Nothing is replaced
    and no line ending is translated, so the proof reads what the synthesis read."""
    rtl, netlist = inputs(tmp_path)
    original = (
        b"wire a; // caf\xe9 \xff\xfe \xe2\x89\xa5 1\r\nwire b;\r\n/* \xc2\xb5m \x80 */ wire c;\r\n"
    )
    (rtl / "chip.v").write_bytes(original)
    fake = FakeYosys("counter_good", equiv=(1, "counter_good", "equiv.log"))
    equiv.check(rtl, "chip", netlist, tmp_path / "scratch", yosys=fake)
    seen = (tmp_path / "scratch" / "chip.v").read_bytes()
    assert len(seen) == len(original) and max(seen) < 0x80  # nothing replaced, comments blanked
    assert seen.startswith(b"wire a;") and seen.count(b"\n") == original.count(b"\n")
    assert b"\nwire b;\r\n" in seen and seen.endswith(b" wire c;\r\n")  # CR LF kept as written
    assert seen.count(b"\r") == 2 and original.count(b"\r") == 3  # a CR inside a comment is blank
    assert (rtl / "chip.v").read_bytes() == original


@pytest.mark.parametrize(
    "outside",
    [
        b"wire a\xc2\xa0b;\n",
        b"wire a\xff;\n",
        b'initial $display("\xc3\xa9");\n',
        b"wire a\x0bb;\n",
    ],
)
def test_a_byte_outside_plain_ascii_in_code_is_not_proven(tmp_path: Path, outside: bytes) -> None:
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_bytes(outside)
    fake = FakeYosys("")
    got = equiv.check(rtl, "chip", netlist, tmp_path / "scratch", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "outside plain ASCII" in got["message"] and fake.calls == []


def test_the_event_list_rule_is_for_the_cores_that_do_not_list_signals_themselves(
    tmp_path: Path,
) -> None:
    text = b"module chip ();\nalways @(a or b) begin: l\n  y = a & b;\nend\nendmodule\n"
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_bytes(text)
    strict = equiv.check(rtl, "chip", netlist, tmp_path / "s1", yosys=FakeYosys(""))
    assert strict["reason"] == "guard_sim_synth_differ" and "event control" in strict["message"]
    fake = FakeYosys("counter_good", equiv=(1, "counter_good", "equiv.log"))
    lax = equiv.check(rtl, "chip", netlist, tmp_path / "s2", yosys=fake, strict_core=False)
    assert lax["reason"] != "guard_sim_synth_differ" and fake.tags == ["equiv"]


def test_a_myhdl_core_is_judged_by_the_level_list_rule(tmp_path: Path) -> None:
    text = (FIXTURES_MYHDL / "bad" / "myalias.v").read_bytes()
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_bytes(text)
    fake = FakeYosys("")
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=fake, strict_core=False)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "plain shape @always_comb writes" in got["message"] and "@always_comb" in got["message"]
    assert fake.calls == []


def test_the_refusal_names_the_agents_function(tmp_path: Path) -> None:
    (tmp_path / "hdl").mkdir()
    (tmp_path / "hdl" / "d.py").write_text(
        "from myhdl import *\n\n@block\ndef top(a, y):\n    @always(a)\n    def pick():\n"
        "        y.next = 1\n    return pick\n"
    )
    assert equiv._hdl_function("pick", tmp_path) == ", hdl/d.py, function pick"
    assert equiv._hdl_function("nope", tmp_path) == ""
    assert equiv._hdl_function("", tmp_path) == ""


def test_an_include_in_the_rtl_is_not_proven(tmp_path: Path) -> None:
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_text('`include "hidden.vh"\n')
    fake = FakeYosys("")
    got = equiv.check(rtl, "chip", netlist, tmp_path / "scratch", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_include"
    assert fake.calls == [] and "chip.v" in got["message"] and NOT_A_PASS in got["message"]


@pytest.mark.parametrize(
    "netlist",
    [
        "module tt_um_chip(); endmodule\nmodule sky130_fd_sc_hd__dlclkp_1(); endmodule\n",
        '(* src = "liberty.lib:1" *)\nmodule tt_um_chip(); endmodule\n',
        '`include "cells.v"\nmodule tt_um_chip(); endmodule\n',
    ],
)
def test_a_netlist_with_a_module_of_its_own_is_an_error(tmp_path: Path, netlist: str) -> None:
    fake = FakeYosys("")
    rtl, path = inputs(tmp_path, netlist)
    got = equiv.check(rtl, "chip", path, tmp_path / "scratch", yosys=fake)
    assert got["verdict"] == "error" and got["reason"] == "netlist_modules"
    assert got["ok"] is False and fake.calls == []


def test_a_stage_1_time_out_is_not_proven_and_says_it_is_not_a_pass(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(None, "counter_good", "equiv.log"),  # killed at the limit; the model was written
        kind_x=(0, "counter_good", "bmc.log"),  # the bounded search for a counterexample: none
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert got["time_limit_hit"] is True and got["stage1"]["status"] == "timeout"
    assert "ran out of time" in got["message"] and NOT_A_PASS in got["message"]
    assert fake.tags == ["equiv", "kind_x"]  # only a bounded search, no induction after a time out
    assert got["cycles_covered"] == 32


def test_a_time_out_before_the_model_exists_is_not_proven(tmp_path: Path) -> None:
    got = go(tmp_path, FakeYosys("", equiv=(None, "counter_good", "equiv.log")))
    # the model was never written, so there is nothing to search: a time limit, not a pass
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert got["time_limit_hit"] is True


def test_a_kind_zero_only_counterexample_is_not_proven(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "shiftnr_m0165", "equiv.log"),
        kind_x=(0, "shiftnr_m0165", "kind_x.log"),  # closes
        kind_zero=(0, "shiftnr_m0165", "kind_zero.log"),  # a difference with all registers at 0
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "power_up_dependent"
    assert fake.tags == ["equiv", "kind_x", "kind_zero"]
    assert "powering up at 0" in got["message"] and "Reset those registers" in got["message"]
    assert got["first_mismatch"]["cycle"] >= 2 and got["trace"]


def test_a_base_case_difference_in_the_def_run_only_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The base case (stage 3) is stubbed to report a difference in the DEF run only; stage 1 and
    the referee are the saved real counter logs."""
    diffs = [{"signal": "uo_out[3]", "rtl": "0", "netlist": "1"}]

    def def_only(r: Any, points: Any, steps: int) -> dict[str, Any]:
        return {
            "status": "cex",
            "run": "DEF",
            "cycle": 3,
            "differs": diffs,
            "inputs": {},
            "outputs_differ": True,
        }

    monkeypatch.setattr(equiv, "_base", def_only)
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "base_case" and not got["ok"]
    assert "cycle 3" in got["message"] and "power up equal" in got["message"]
    assert got["base"]["run"] == "DEF"


def test_a_base_case_x_difference_on_an_internal_point_only_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def internal(r: Any, points: Any, steps: int) -> dict[str, Any]:
        diffs = [{"signal": "core.count[2]", "rtl": "0", "netlist": "x"}]
        return {"status": "cex", "run": "X", "cycle": 2, "differs": diffs, "outputs_differ": False,
                "inputs": {}}  # fmt: skip

    monkeypatch.setattr(equiv, "_base", internal)
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "base_case"
    assert "internal signal" in got["message"]


def test_unproven_points_with_a_clean_search_are_not_proven(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0043", "equiv.log"),
        kind_x=(0, "counter_m0043", "kind_x.log"),
        kind_zero=(0, "counter_m0043", "kind_zero.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "unproven_points"
    assert got["cycles_covered"] == 32 and got["stage1"]["unproven"] == 2
    assert "2 point(s) could not be proven" in got["message"]
    assert "first 32 cycles from reset" in got["message"]


# -- error --------------------------------------------------------------------------------------


def test_a_loop_and_a_cell_without_a_function_are_errors(tmp_path: Path) -> None:
    got = go(tmp_path, FakeYosys("counter_good", equiv=(1, "loop", "equiv.log")))
    assert got["verdict"] == "error" and got["reason"] == "structure" and not got["ok"]
    assert "combinational loop" in got["message"]
    (tmp_path / "b").mkdir()
    got = go(tmp_path / "b", FakeYosys("counter_good", equiv=(1, "clockgate", "equiv.log")))
    assert got["verdict"] == "error" and got["reason"] == "cells" and not got["ok"]


def test_a_yosys_failure_in_stage_1_is_an_error(tmp_path: Path) -> None:
    fake = FakeYosys("counter_good", equiv=(1, "inline", "ERROR: syntax error in line 3\n"))
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and got["reason"] == "yosys_failed" and not got["ok"]
    assert got["detail"] == ["ERROR: syntax error in line 3"]
    assert fake.tags == ["equiv"]


def test_a_yosys_failure_in_the_base_case_is_an_error(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(1, "inline", "ERROR: boom\n"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and got["reason"] == "yosys_failed" and not got["ok"]
    assert got["base"] == {"status": "fail"} and fake.tags == ["equiv", "base"]


def test_a_yosys_failure_in_the_referee_is_an_error(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(1, "inline", "ERROR: boom\n"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and not got["ok"] and got["reason"] == "yosys_failed"


@pytest.mark.parametrize("text", ["wire a = 1'bx;", "cell u (.A(1'bz));", "x(4'b0z01)"])
def test_an_x_or_z_constant_in_the_netlist_is_an_error_and_nothing_runs(
    tmp_path: Path, text: str
) -> None:
    fake = FakeYosys("")
    got = go(tmp_path, fake, netlist=f"module tt_um_chip();\n{text}\nendmodule\n")
    assert got["verdict"] == "error" and got["reason"] == "xz_constant" and not got["ok"]
    assert fake.calls == [] and "unknown constant" in got["message"]


def test_missing_files_are_an_error(tmp_path: Path) -> None:
    got = equiv.check(
        tmp_path / "nowhere", "chip", tmp_path / "n.v", tmp_path / "s", yosys=FakeYosys("")
    )
    assert got["verdict"] == "error" and got["reason"] == "bad_input" and got["ok"] is False


def test_an_oversized_netlist_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(equiv, "NETLIST_MAX_BYTES", 10)
    got = go(tmp_path, FakeYosys(""))
    assert got["verdict"] == "error" and got["reason"] == "bad_input"


# -- the result is clipped to the host's limits ----------------------------------------------------


def test_lists_and_strings_are_clipped() -> None:
    out: dict[str, Any] = {
        "detail": ["d" * 5000] * 20,
        "first_mismatch": {
            "cycle": 3,
            "differs": [{"signal": "s" * 500, "rtl": "1" * 500, "netlist": None}] * 100,
        },
        "base": {"status": "cex", "differs": [{"signal": "x", "rtl": "0", "netlist": "1"}] * 100},
        "stage1": {"unproven_signals": ["u" * 500] * 100},
    }
    got = equiv._clamp(out)
    from siliconsmith.worker import limits as lim

    assert len(got["detail"]) == lim.MAX_EQUIV_DETAIL
    assert all(len(x) <= lim.EQUIV_DETAIL_LINE_MAX for x in got["detail"])
    diffs = got["first_mismatch"]["differs"]
    assert len(diffs) == lim.MAX_EQUIV_DIFFS
    assert len(diffs[0]["signal"]) <= lim.EQUIV_SIGNAL_MAX
    assert len(diffs[0]["rtl"]) <= lim.EQUIV_VALUE_MAX and diffs[0]["netlist"] is None
    assert len(got["base"]["differs"]) == lim.MAX_EQUIV_DIFFS
    assert len(got["stage1"]["unproven_signals"]) == lim.MAX_EQUIV_DIFFS
    trace = [{"cycle": c, "inputs": {"rst_n": "1"}, "rtl": {}, "netlist": {}} for c in range(1, 60)]
    short = equiv._trace(trace, {"cycle": 40, "differs": []})
    assert len(short) == lim.MAX_EQUIV_TRACE and short[-1]["cycle"] == 40


# -- a combinational block that reads a signal before it writes it ---------------------------------

ALWCOMBORDER = (
    "%Warning-ALWCOMBORDER: chip.v:4:5: Always_comb variable driven after use: 't'\n"
    "                                  : ... note: In instance 'chip'\n"
    "    4 |     t = cnt;\n      |     ^\n"
)


STAR_TREE = {
    "type": "NETLIST",
    "modulesp": [{"type": "ALWAYS", "addr": "(A)", "keyword": "always", "loc": "e,2:3,2:9"}],
}


def il(*places: str) -> str:
    """RTLIL with one process per source position (`file:L.C-L.C`)."""
    body = "".join(
        f'  attribute \\src "{p}"\n  process $proc$n{i}\n  end\n' for i, p in enumerate(places)
    )
    return f"module \\tt_um_chip\n{body}end\n"


STAR_IL = il("chip.v:2.3-5.6")  # Yosys keeps the `always @*` of star_core


def answer_tree(argv: list[str], cwd: Path) -> equiv.LintRun | None:
    """The `--json-only` run of the purity check, for a fake Verilator: the tree of star_core."""
    if "--json-only" not in argv:
        return None
    write_trees(cwd, json.dumps(STAR_TREE))
    return equiv.LintRun(0, "")


def star_core(tmp_path: Path) -> tuple[Path, Path]:
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_text(
        "module tt_um_chip();\n  always @* begin\n    y = t;\n    t = cnt;\n  end\nendmodule\n"
    )
    return rtl, netlist


def check_star(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: equiv.LintRun | None
) -> dict[str, Any]:
    monkeypatch.setattr(equiv, "_run_verilator", lambda argv, cwd: answer_tree(argv, cwd) or run)
    rtl, netlist = star_core(tmp_path)
    fake = FakeYosys("")
    fake.elab_il = STAR_IL
    return equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=fake)


def test_a_block_that_reads_before_it_writes_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], str]] = []

    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun:
        answered = answer_tree(argv, cwd)
        if answered is not None:
            return answered
        seen.append((argv, (cwd / "chip.v").read_text()))
        return equiv.LintRun(0, ALWCOMBORDER)

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    rtl, netlist = star_core(tmp_path)
    fake = FakeYosys("")
    fake.elab_il = STAR_IL
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "chip.v, line 4" in got["message"] and "`t`" in got["message"]
    assert "reads a signal before it writes it" in got["message"] and fake.tags == ["elab"]
    argv, copy = seen[0]
    assert argv[:2] == ["verilator", "--lint-only"] and "-Wno-fatal" in argv and "-Wall" in argv
    assert argv[-2:] == ["project.v", "chip.v"] and "tt_um_chip" in argv
    assert "always_comb" in copy and "@*" not in copy and copy.count("\n") == 6  # lines stay


def test_the_comb_pass_has_the_emit_lints_time() -> None:
    assert equiv.COMB_LINT_TIMEOUT_S == equiv.emit.TOOL_TIMEOUT_S == 120


def test_a_timeout_is_not_proven_with_the_time_limit_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = check_star(tmp_path, monkeypatch, equiv.LintRun(-1, "", True))
    assert got["verdict"] == "not_proven" and got["reason"] == "time_limit"
    assert "did not finish within 120 s" in got["message"]


def test_a_finding_seen_before_the_timeout_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got = check_star(tmp_path, monkeypatch, equiv.LintRun(-1, ALWCOMBORDER, True))
    assert got["reason"] == "guard_sim_synth_differ" and "reads a signal before" in got["message"]


@pytest.mark.parametrize(
    ("run", "line"),
    [
        (equiv.LintRun(1, "%Error: chip.v:3:5: syntax error, unexpected always_comb\n"), "line 3"),
        (
            equiv.LintRun(1, "%Error: chip.v:2:3: Unsupported: foo\n%Error: chip.v:9:1: later\n"),
            "line 2",
        ),
        (equiv.LintRun(1, "verilator: something odd happened"), "something odd happened"),
        (equiv.LintRun(1, ""), "failed"),
        # an ALWCOMBORDER in the output beats an error elsewhere (the finding is the answer)
    ],
)
def test_a_copy_that_does_not_lint_is_not_proven_naming_the_first_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: equiv.LintRun, line: str
) -> None:
    got = check_star(tmp_path, monkeypatch, run)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "does not lint" in got["message"] and "always_comb" in got["message"]
    assert line in got["message"]


def test_a_finding_beats_an_error_in_the_same_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = equiv.LintRun(1, "%Error: chip.v:3:5: syntax error\n" + ALWCOMBORDER)
    got = check_star(tmp_path, monkeypatch, run)
    assert "reads a signal before it writes it" in got["message"]


@pytest.mark.parametrize(
    "output",
    [
        "",
        "%Warning-MULTIDRIVEN: chip.v:3:5: Signal has multiple driving blocks: 'y'\n",
        "%Warning-WIDTHTRUNC: chip.v:3:5: Operator ASSIGN expects 8 bits\n",
        "%Warning-ALWCOMBORDER: elsewhere.v:4:5: Always_comb variable driven after use: 't'\n",
        "%Warning-ALWCOMBORDER: Always_comb variable driven after use: 't'\n",  # no position
    ],
)
def test_a_clean_copy_or_other_warnings_add_no_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    got = check_star(tmp_path, monkeypatch, equiv.LintRun(0, output))
    assert got["reason"] != "guard_sim_synth_differ" and got["reason"] != "time_limit"


def test_a_verilator_that_cannot_be_started_is_inconclusive_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        got = check_star(tmp_path, monkeypatch, None)
    assert got["reason"] != "guard_sim_synth_differ" and got["reason"] != "time_limit"
    assert "could not be started" in caplog.text


def test_no_star_block_means_no_verilator_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun:
        raise AssertionError("verilator was run")

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_text(
        "module tt_um_chip();\n  always @(posedge clk) y <= a;\nendmodule\n"
    )
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=FakeYosys(""))
    assert got["reason"] != "guard_sim_synth_differ"


def test_run_verilator_reports_start_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    assert REAL_RUN_VERILATOR(["/nonexistent-verilator"], Path(".")) is None  # cannot start

    def boom(*a: Any, **k: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd="verilator", timeout=1, stderr="partial")

    monkeypatch.setattr(equiv.subprocess, "run", boom)
    run = REAL_RUN_VERILATOR(["verilator"], Path("."))
    assert run is not None and run.timed_out and run.output == "partial"


# -- the function-purity check (Verilator's syntax tree) -------------------------------------------

FUNC_RTL = (
    "module tt_um_chip();\n  reg [7:0] cnt;\n  function automatic [7:0] rd(input d);\n"
    "    rd = cnt;\n  endfunction\nendmodule\n"
)
TREE_META = '{"files": {"e": {"filename": "chip.v"}}}'
IMPURE_TREE = {
    "type": "NETLIST",
    "modulesp": [
        {"type": "VAR", "addr": "(S)", "name": "cnt", "loc": "e,2:7,2:10"},
        {
            "type": "FUNC",
            "addr": "(F)",
            "name": "rd",
            "loc": "e,3:28,3:30",
            "stmtsp": [
                {
                    "type": "VARREF",
                    "addr": "(r)",
                    "name": "cnt",
                    "varp": "(S)",
                    "access": "RD",
                    "loc": "e,4:10,4:13",
                }
            ],
        },
    ],
}


def purity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run: equiv.LintRun | None,
    *,
    tree: str | None = None,
    text: str = FUNC_RTL,
    fake: FakeYosys | None = None,
    before: str = NO_GENERATE,
) -> tuple[tuple[str, str] | None, list[list[str]]]:
    """`equiv._tree_checks` with a fake Verilator that answers `run` and writes `tree` (and
    `before`, the tree before V3Param), and a fake Yosys (`fake`, answering the "elab" call).
    Gives `(reason, sentence)` or None."""
    argvs: list[list[str]] = []

    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun | None:
        argvs.append(argv)
        if tree is not None:
            write_trees(cwd, tree, before)
        return run

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    sources = [("project.v", "// wrapper\n"), ("chip.v", text)]
    r = equiv._Run(tmp_path / "s", fake or FakeYosys(""), equiv.Clock(60, 120), time.monotonic())
    got = equiv._tree_checks(r, sources, "chip")
    if got is None:
        return None, argvs
    sentence = got["message"].removeprefix("Could not prove equivalence: ")
    return (got["reason"], sentence.removesuffix(f" {NOT_A_PASS}")), argvs


def test_a_design_of_continuous_assignments_only_gets_the_tree_checks_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 13 g08/x01genxassign: `localparam Q = 1 / Z;` steering a generate `case` of plain
    `assign`s. No always block, function or task, and the x parameter must still be refused."""
    text = (
        "module tt_um_chip(input [7:0] a, output [7:0] y);\n  localparam integer Z = 0;\n"
        "  localparam integer Q = 1 / Z;\n  case (Q)\n    (1 / Z) + 1: begin : g assign y = a; "
        "end\n    default: begin : g assign y = ~a; end\n  endcase\nendmodule\n"
    )
    tree = {"type": "NETLIST", "modulesp": [
        {"type": "MODULE", "addr": "(M)", "name": "tt_um_chip", "loc": "e,1:1,1:7", "level": 1},
        {"type": "VAR", "addr": "(Q)", "name": "Q", "varType": "LPARAM", "isParam": True,
         "loc": "e,3:22,3:23", "valuep": [{"type": "CONST", "name": "32'hxxxxxxxx",
                                           "loc": "e,3:26,3:31"}]},
    ]}  # fmt: skip
    fake = fake_elab("")
    got, argvs = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree), text=text, fake=fake
    )
    assert len(argvs) == 1 and "--json-only" in argvs[0]  # the tree was built
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "computes the parameter `Q` with an x or z bit" in got[1]


def test_an_impure_function_is_not_proven_with_one_clean_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got, argvs = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(IMPURE_TREE))
    assert got == (
        "guard_sim_synth_differ",
        "the function `rd` (chip.v, line 3) reads `cnt` (line 4), which is not one of its "
        "arguments: a simulator's always @* does not re-run when it changes; pass it in as an "
        "argument.",
    )
    argv = argvs[0]
    assert argv[:2] == ["verilator", "--json-only"] and "-Wno-fatal" in argv
    assert "-fno-dfg" in argv  # else a one-assignment block has no node at its keyword
    assert argv[-2:] == ["project.v", "chip.v"] and "tt_um_chip" in argv


def test_a_pure_tree_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = {
        "type": "NETLIST",
        "modulesp": [{"type": "FUNC", "addr": "(F)", "name": "rd", "loc": "e,3:28,3:30"}],
    }
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree))
    assert got is None


STAR_TEXT = "module tt_um_chip();\n  always @* begin\n    y = t;\n  end\nendmodule\n"
EMPTY_TREE = json.dumps({"type": "NETLIST", "modulesp": []})
STAR_NODE = {"type": "ALWAYS", "keyword": "always", "loc": "e,2:3,2:9"}


def fake_elab(il_text: str, **answers: tuple[int | None, str, str]) -> FakeYosys:
    fake = FakeYosys("", **answers)
    fake.elab_il = il_text
    return fake


def test_a_block_that_yosys_keeps_and_verilator_dropped_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generate branch Verilator drops (a constant `1/0` is x to Yosys, folded by Verilator)."""
    fake = fake_elab(il("chip.v:2.3-5.6"))
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT, fake=fake
    )
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "Yosys keeps the always block at chip.v, line 2" in got[1]
    script = (tmp_path / "s/elab/elab.ys").read_text()
    assert fake.tags == ["elab"] and "write_rtlil elab.il" in script


def test_a_difference_in_the_elaboration_is_reported_before_an_impure_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = (
        "module tt_um_chip();\n  always @* y = t;\n  function automatic rd(input a);\nendmodule\n"
    )
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(IMPURE_TREE), text=text,
        fake=fake_elab(il("chip.v:2.3-2.18")),
    )  # fmt: skip
    assert got is not None and "Yosys keeps the always block at chip.v, line 2" in got[1]


def test_a_block_that_both_tools_keep_passes_and_one_neither_has_is_no_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = json.dumps({"type": "NETLIST", "modulesp": [STAR_NODE]})
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=tree, text=STAR_TEXT,
        fake=fake_elab(il("chip.v:2.3-5.6")),
    )  # fmt: skip
    assert got is None
    # a block that is in the text but in neither elaboration (a generate branch not taken)
    got, _ = purity(
        tmp_path / "x", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT,
        fake=fake_elab(il()),
    )  # fmt: skip
    assert got is None


def test_an_elaboration_that_times_out_is_a_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_elab("", elab=(None, "inline", ""))
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT, fake=fake
    )
    assert got is not None and got[0] == "time_limit"
    assert "elaboration" in got[1]


def test_a_failing_elaboration_or_an_unreadable_result_is_never_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failing = fake_elab("", elab=(1, "inline", "ERROR: syntax error in line 3\n"))
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT, fake=failing
    )
    assert got is not None and "elaboration" in got[1]
    # exit 0 but a process Yosys gives no position
    odd = fake_elab("module \\m\n  process $p\n  end\nend\n")
    got, _ = purity(
        tmp_path / "x", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT,
        fake=odd,
    )  # fmt: skip
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "could not read the elaboration of the RTL" in got[1]


OUT_OF_RANGE = (
    "i15.v:8: Warning: Range select out of bounds on signal `\\pick$func$i15.v:11$2.v': "
    "Setting result bit to undef.\n"
)


def test_a_select_outside_the_range_that_yosys_finds_is_named_with_its_line() -> None:
    got = equiv._yosys_outside(f"1. Executing Verilog frontend.\n{OUT_OF_RANGE}End of script.\n")
    assert got is not None and got.startswith("the RTL (i15.v, line 8) selects outside the range")
    assert "Range select out of bounds on signal `\\pick$func$i15.v:11$2.v'" in got
    assert "Keep every select inside the declared range. A select in an arm of `?:`" in got
    assert "a constant condition never selects is refused too" in got
    assert got.endswith("guard it with an `if` instead (`if (i < 7)`), and Yosys drops it.")
    # round 16 bpart: `v[k +: 2]` with k = 5 of a 4-bit `v`, a part-select
    part = (
        "bpart.v:4: Warning: Range select [6:5] out of bounds on signal "
        "`\\pk$func$bpart.v:7$2.v': Setting all 2 result bits to undef.\n"
    )
    got = equiv._yosys_outside(part)
    assert got is not None and got.startswith("the RTL (bpart.v, line 4) selects outside")
    assert '"Range select [6:5] out of bounds on signal' in got
    bare = equiv._yosys_outside("Warning: Range select out of bounds on signal `\\v'.\n")
    assert bare is not None and bare.startswith("the RTL selects outside")
    assert equiv._yosys_outside("Warning: Replacing memory \\m with list of registers.\n") is None
    assert equiv._yosys_outside("") is None


def test_a_select_outside_the_range_in_the_elaboration_is_refused_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yosys warns about `v[6]` of a 4-bit `v` once it inlines `pick(ui_in[3:0], 6)`: a simulator
    reads x there (round 15). Judged after every other finding about the RTL."""
    tree = json.dumps({"type": "NETLIST", "modulesp": [STAR_NODE]})
    fake = fake_elab(il("chip.v:2.3-5.6"), elab=(0, "inline", OUT_OF_RANGE))
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=tree, text=STAR_TEXT, fake=fake
    )
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert got[1].startswith("the RTL (i15.v, line 8) selects outside the range"), got[1]
    # a block Yosys keeps and Verilator dropped is named first
    fake = fake_elab(il("chip.v:2.3-5.6"), elab=(0, "inline", OUT_OF_RANGE))
    got, _ = purity(
        tmp_path / "x", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT,
        fake=fake,
    )  # fmt: skip
    assert got is not None and "Yosys keeps the always block at chip.v, line 2" in got[1]


def test_a_stale_elaboration_is_never_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An `elab.il` left by an earlier run is deleted before Yosys runs: a Yosys that writes
    nothing (exit 0) is unreadable, never the old file's clean result."""
    stale = tmp_path / "s" / "elab" / "elab.il"
    stale.parent.mkdir(parents=True)
    stale.write_text("module \\tt_um_chip\nend\n")  # no process: would match anything
    fake = fake_elab("")
    fake.elab_il = None
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, text=STAR_TEXT, fake=fake
    )
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "could not read the elaboration of the RTL" in got[1] and not stale.exists()


def test_yosys_does_not_run_when_verilator_gave_no_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_elab("")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(1, "%Error: x\n"), fake=fake)
    assert got is not None and fake.calls == []


def test_a_divergent_elaboration_stops_check_before_stage_1_and_a_matched_one_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def verilator(tree: str) -> Any:
        def run(argv: list[str], cwd: Path) -> equiv.LintRun | None:
            if "--json-only" not in argv:
                return None
            write_trees(cwd, tree)
            return equiv.LintRun(0, "")

        return run

    rtl, netlist = star_core(tmp_path)
    monkeypatch.setattr(equiv, "_run_verilator", verilator(EMPTY_TREE))
    fake = fake_elab(STAR_IL)
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s1", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "elaborate the RTL differently" in got["message"] and fake.tags == ["elab"]
    assert got["seconds"]["elab"] >= 0 and got["seconds"]["tree"] >= 0

    monkeypatch.setattr(equiv, "_run_verilator", verilator(json.dumps(STAR_TREE)))
    fake = fake_elab(STAR_IL, equiv=(1, "inline", "ERROR: stop here\n"))
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s2", yosys=fake)
    assert fake.tags == ["elab", "equiv"]  # matched: the check goes on to stage 1
    assert got["reason"] != "guard_sim_synth_differ"


def test_a_purity_run_that_times_out_is_a_time_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(-1, "", True))
    assert got is not None and got[0] == "time_limit"


@pytest.mark.parametrize(
    ("run", "tree", "shown"),
    [
        (
            equiv.LintRun(1, "%Warning-X: a\n%Error: chip.v:3:5: syntax error\n"),
            None,
            "syntax error",
        ),
        (equiv.LintRun(1, ""), None, "failed"),
        (equiv.LintRun(0, ""), None, "no usable syntax tree"),  # no tree.json written
        (equiv.LintRun(0, ""), "{not json", "no usable syntax tree"),
        (equiv.LintRun(0, ""), "[]", "the tree is not an object"),
        (
            equiv.LintRun(0, ""),
            json.dumps({"modulesp": [{"type": "FUNC", "addr": "(F)", "loc": "z,1:1,1:2"}]}),
            "no readable position",
        ),
    ],
)
def test_a_purity_run_that_cannot_be_read_is_not_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    run: equiv.LintRun,
    tree: str | None,
    shown: str,
) -> None:
    got, _ = purity(tmp_path, monkeypatch, run, tree=tree)
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert got[1].startswith("the check could not read the RTL with Verilator: ")
    assert shown in got[1]


def test_a_purity_run_that_cannot_start_is_inconclusive_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        got, _ = purity(tmp_path, monkeypatch, None)
    assert got is None and "Verilator-based checks" in caplog.text


def test_check_refuses_an_impure_function_before_any_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun:
        write_trees(cwd, json.dumps(IMPURE_TREE))
        return equiv.LintRun(0, "")

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_text(FUNC_RTL)
    fake = FakeYosys("")
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "the function `rd` (chip.v, line 3) reads `cnt` (line 4)" in got["message"]
    assert fake.tags == ["elab"]


# The star_core block `y = t; t = cnt;` as Verilator's tree: t is read before it is written.
EARLY_TREE = {
    "type": "NETLIST",
    "modulesp": [
        {"type": "BASICDTYPE", "addr": "(D)", "keyword": "logic", "range": "7:0"},
        {"type": "VAR", "addr": "(T)", "name": "t", "dtypep": "(D)", "loc": "e,1:1,1:2"},
        {"type": "VAR", "addr": "(Y)", "name": "y", "dtypep": "(D)", "loc": "e,1:1,1:2"},
        {"type": "VAR", "addr": "(C)", "name": "cnt", "dtypep": "(D)", "loc": "e,1:1,1:2"},
        {
            "type": "ALWAYS",
            "addr": "(A)",
            "keyword": "always",
            "loc": "e,2:3,2:9",
            "stmtsp": [
                {
                    "type": "ASSIGN",
                    "lhsp": [{"type": "VARREF", "varp": "(Y)", "access": "WR", "loc": "e,3:5,3:6"}],
                    "rhsp": [{"type": "VARREF", "varp": "(T)", "access": "RD", "loc": "e,3:9,3:10",
                              "name": "t"}],
                },
                {
                    "type": "ASSIGN",
                    "lhsp": [{"type": "VARREF", "varp": "(T)", "access": "WR", "loc": "e,4:5,4:6"}],
                    "rhsp": [{"type": "VARREF", "varp": "(C)", "access": "RD",
                              "loc": "e,4:9,4:12"}],
                },
            ],
        },
    ],
}  # fmt: skip
STAR_RTL = "module tt_um_chip();\n  always @* begin\n    y = t;\n    t = cnt;\n  end\nendmodule\n"


def test_a_read_before_a_write_on_some_path_is_refused_after_a_matched_elaboration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = fake_elab(STAR_IL)
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(EARLY_TREE), text=STAR_RTL,
        fake=fake,
    )  # fmt: skip
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert got[1].startswith("the RTL (chip.v, line 3) has a combinational block that can read `t`")
    assert "on some path through the block" in got[1] and fake.tags == ["elab"]
    # a block written in the right order passes
    good = json.loads(json.dumps(EARLY_TREE))
    good["modulesp"][4]["stmtsp"].reverse()
    got, _ = purity(
        tmp_path / "x", monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(good), text=STAR_RTL,
        fake=fake_elab(STAR_IL),
    )  # fmt: skip
    assert got is None


def test_a_statement_the_walk_does_not_know_is_never_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    odd = json.loads(json.dumps(EARLY_TREE))
    odd["modulesp"][4]["stmtsp"].append({"type": "WAIT"})
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(odd), text=STAR_RTL,
        fake=fake_elab(STAR_IL),
    )  # fmt: skip
    assert got is not None and "no usable syntax tree" in got[1]


def test_an_x_parameter_is_refused_first_without_running_the_elaboration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = json.loads(json.dumps(EARLY_TREE))
    tree["modulesp"].append(
        {"type": "VAR", "addr": "(Q)", "name": "Q", "varType": "LPARAM", "isParam": True,
         "loc": "e,1:3,1:4", "valuep": [{"type": "CONST", "name": "32'hxxxxxxxx",
                                         "loc": "e,1:7,1:10"}]}
    )  # fmt: skip
    fake = fake_elab(STAR_IL)
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree), text=STAR_RTL,
        fake=fake,
    )  # fmt: skip
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "computes the parameter `Q` with an x or z bit" in got[1]
    assert fake.calls == []  # the root cause, before Yosys elaborates anything


# -- round 14: generate conditions and the simulator's constants (`_tree_checks`) --------------

# `if ((1 / Z) == 0) begin : g ... end` as Verilator's tree has it before V3Param (b03arr)
X_CONDITION = json.dumps(
    {
        "type": "NETLIST",
        "modulesp": [
            {
                "type": "GENIF",
                "loc": "e,3:3,3:5",
                "condp": [
                    {
                        "type": "DIV",
                        "loc": "e,3:10,3:11",
                        "lhsp": [{"type": "CONST", "name": "?32?sh1", "loc": "e,3:8,3:9"}],
                        "rhsp": [{"type": "VARREF", "name": "Z", "loc": "e,3:12,3:13"}],
                    }
                ],
            }
        ],
    }
)


def test_a_generate_condition_that_can_be_x_is_refused_before_the_simulator_and_yosys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[list[str]] = []
    monkeypatch.setattr(equiv, "_run_icarus", lambda argv, cwd: started.append(argv))
    fake = fake_elab("")
    got, argvs = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, before=X_CONDITION,
        fake=fake,
    )  # fmt: skip
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "(chip.v, line 3) has a `/` or `%` whose divisor is not a plain nonzero number" in got[1]
    assert "--dump-tree-json" in argvs[0] and "--json-only" in argvs[0]
    assert started == [] and fake.tags == []


@pytest.mark.parametrize("stages", [[], [EARLY_STAGE, "Vtt_um_chip_009_linkinc.tree.json"]])
def test_without_exactly_one_tree_of_the_stage_before_the_parameters_it_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stages: list[str]
) -> None:
    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun:
        write_trees(cwd, EMPTY_TREE)
        (cwd / "obj_dir" / EARLY_STAGE).unlink()
        for name in stages:
            (cwd / "obj_dir" / name).write_text(NO_GENERATE)
        return equiv.LintRun(0, "")

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    r = equiv._Run(tmp_path / "s", fake_elab(""), equiv.Clock(60, 120), time.monotonic())
    got = equiv._tree_checks(r, [("project.v", "// wrapper\n"), ("chip.v", FUNC_RTL)], "chip")
    assert got is not None and got["reason"] == "guard_sim_synth_differ"
    assert "no tree of the stage before the parameters are evaluated" in got["message"]


SIM_TABLE = (
    ':file_names 5;\n    "N/A";\n    "<interactive>";\n    "-";\n    "project.v";\n    "chip.v";\n'
)
# Icarus writes every parameter after the scope it is in (here the top module)
SIM_TOP = 'S_0x9 .scope module, "tt_um_chip" "tt_um_chip" 3 7;\n'
SIM_CLEAN = (
    SIM_TOP + 'P_0x1 .param/l "W" 1 4 2, +C4<00000000000000000000000000001000>;\n' + SIM_TABLE
)
SIM_X = SIM_TOP + 'P_0x1 .param/l "Q" 1 4 8, C4<x>;\n' + SIM_TABLE  # xsel: Q = A[6] of a 4-bit A
SELECT = "chip.v:9: warning: Constant bit select [6] is after A[3:0].\n"


def simulator(
    monkeypatch: pytest.MonkeyPatch, run: equiv.LintRun | None, vvp: str | None
) -> list[tuple[list[str], dict[str, str]]]:
    """A fake Icarus that answers `run` and writes `vvp` as `sim.vvp`; records each call."""
    calls: list[tuple[list[str], dict[str, str]]] = []

    def icarus(argv: list[str], cwd: Path) -> equiv.LintRun | None:
        calls.append((argv, {f.name: f.read_text() for f in cwd.glob("*.v")}))
        if vvp is not None:
            (cwd / "sim.vvp").write_text(vvp)
        return run

    monkeypatch.setattr(equiv, "_run_icarus", icarus)
    return calls


@pytest.mark.parametrize(
    ("run", "vvp", "shown"),
    [
        # xsel: Verilator folds `A[6]` to 0, the simulator reads x
        (equiv.LintRun(0, ""), SIM_X, "(chip.v, line 8) gives the parameter `Q` a value with an x"),
        (equiv.LintRun(0, SELECT), SIM_CLEAN, "(chip.v, line 9) selects outside the range"),
        (equiv.LintRun(0, SELECT), SIM_X, "line 8) gives the parameter `Q`"),  # the value first
        # it does not compile: the first error, or the last line, is named
        (
            equiv.LintRun(3, "chip.v:2: warning: odd\nchip.v:7: sorry: break statements\n"),
            None,
            "cannot compile the RTL (chip.v:7: sorry: break statements)",
        ),
        (
            equiv.LintRun(5, "chip.v:4: syntax error\nI give up.\n"),
            None,
            "(chip.v:4: syntax error)",
        ),
        (equiv.LintRun(1, ""), SIM_CLEAN, "cannot compile the RTL (it failed)"),
        (equiv.LintRun(0, ""), None, "cannot compile the RTL"),  # no output file
        (
            equiv.LintRun(0, ""),
            SIM_TOP + 'P_0x1 .param/l "Q" 1 4 8, C4<x;\n' + SIM_TABLE,
            "could not read",
        ),
        (equiv.LintRun(0, ""), SIM_CLEAN.removeprefix(SIM_TOP), "outside every scope"),
        (equiv.LintRun(0, ""), SIM_CLEAN.replace(":file_names", ":nofiles"), "could not read"),
    ],
)
def test_the_simulators_constants_refuse_an_x_and_it_must_compile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: equiv.LintRun, vvp: str | None, shown: str
) -> None:
    calls = simulator(monkeypatch, run, vvp)
    fake = fake_elab("")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is not None and got[0] == "guard_sim_synth_differ" and shown in got[1], got
    assert fake.tags == []  # named before Yosys's elaboration
    argv, files = calls[0]
    assert argv[:3] == ["iverilog", "-g2012", "-Wselect-range"]
    assert argv[-4:] == ["-s", "tt_um_chip", "project.v", "chip.v"]
    assert set(files) == {"project.v", "chip.v"}  # the cleaned RTL that Yosys reads


ASSERT_DIMS = "chip.v:6: assert: netmisc.cc:1821: failed assertion packed_dims.size() == 1"
ASSERT_DIM = "chip.v:2: assert: eval_tree.cc:2175: failed assertion dim == 1"
PACKED_ADVICE = "declare it as a plain vector (`localparam [7:0] P`)"


@pytest.mark.parametrize(
    ("run", "shown"),
    [
        # round 14: `localparam [1:0][3:0] P = 8'h5a;` (and `P[1]`); `$size(K2, 2)` of such a K2
        (
            equiv.LintRun(134, f"{ASSERT_DIMS}\nAborted (core dumped)\n"),
            [ASSERT_DIMS, PACKED_ADVICE],
        ),
        (equiv.LintRun(-6, f"{ASSERT_DIM}\nAborted (core dumped)\n"), [ASSERT_DIM, PACKED_ADVICE]),
        # an assertion the check does not know, and an abort with no assertion line
        (
            equiv.LintRun(134, "chip.v:3: warning: odd\nchip.v:9: assert: x.cc:5: failed z\n"),
            ["chip.v:9: assert: x.cc:5: failed z", "simpler form"],
        ),
        (equiv.LintRun(-11, "Segmentation fault\n"), ["(`Segmentation fault`)", "simpler form"]),
        (equiv.LintRun(-6, ""), ["(`exit code -6`)", "simpler form"]),
    ],
)
def test_an_icarus_crash_is_refused_with_its_assertion_and_what_to_write_instead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run: equiv.LintRun, shown: list[str]
) -> None:
    """Icarus 12 aborts on some constructs: still a refusal (fail closed), but the message
    quotes the assertion line, says it is a limit of the simulator and, for a known assertion,
    what to write instead; never the bare "Aborted (core dumped)"."""
    simulator(monkeypatch, run, None)
    fake = fake_elab("")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is not None and got[0] == "guard_sim_synth_differ", got
    assert "internal error" in got[1] and "limit of the simulator" in got[1], got[1]
    assert "cannot compile" not in got[1]
    for text in shown:
        assert text in got[1], (text, got[1])
    assert fake.tags == []


def test_a_clean_simulator_run_goes_on_to_the_elaboration_and_a_stale_output_is_never_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    simulator(monkeypatch, equiv.LintRun(0, "chip.v:3: warning: @* found no sensitivities\n"),
              SIM_CLEAN)  # fmt: skip
    fake = fake_elab("module \\tt_um_chip\nend\n")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is None and fake.tags == ["elab"]
    # the next run fails and writes nothing: the output of the run before must not count
    simulator(monkeypatch, equiv.LintRun(2, "chip.v:1: syntax error\n"), None)
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is not None and "cannot compile the RTL" in got[1]


# c02ifnot (round 15): `localparam integer M = pick(4'b1010, 6);` on line 11 of chip.v, where
# `pick` reads `v[6]` of a 4-bit `v` and returns `!v[k] ? 0 : 1`. Verilator folds it to 0, the
# simulator reads x, `if (!x)` takes the else branch and M is 1.
def param_tree(value: str) -> str:
    m = {"type": "VAR", "addr": "(V)", "name": "M", "varType": "LPARAM", "isParam": True,
         "loc": "e,11:20,11:21",
         "valuep": [{"type": "CONST", "name": value, "loc": "e,11:24,11:28"}]}  # fmt: skip
    top = {"type": "MODULE", "addr": "(T)", "name": "tt_um_chip", "level": 1, "stmtsp": [m]}
    return json.dumps({"type": "NETLIST", "modulesp": [top]})


SIM_M = SIM_TOP + 'P_0x1 .param/l "M" 1 4 11, +C4<00000000000000000000000000000001>;\n' + SIM_TABLE


@pytest.mark.parametrize(
    ("verilator", "refused"), [("32'sh0", True), ("32'sh1", False)], ids=["differ", "agree"]
)
def test_a_parameter_the_simulator_and_verilator_compute_differently_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verilator: str, refused: bool
) -> None:
    simulator(monkeypatch, equiv.LintRun(0, ""), SIM_M)
    fake = fake_elab("module \\tt_um_chip\nend\n")
    got, _ = purity(
        tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=param_tree(verilator), fake=fake
    )
    if refused:
        assert got is not None and got[0] == "guard_sim_synth_differ", got
        assert "(chip.v, line 11) gives the parameter `M` the value 1 in a simulator" in got[1]
        assert "but 0 in Verilator's elaboration" in got[1]
        assert fake.tags == []  # named before Yosys's elaboration
    else:
        assert got is None and fake.tags == ["elab"]


# Round 16 breal: `localparam real R = pick(4'b1010, 6);` on line 7 of chip.v. Before V3Param the
# value holds the call (`pick` reads `v[k]`); Verilator's value is the real 0.0, Icarus's 1.0.
def pick_tree(where: str) -> str:
    """The `linkinc` tree with `pick` and one call `pick(4'b1010, 6)` at line 7: in the value of
    the real localparam R (`where="value"`) or in an `assign` (`where="assign"`)."""

    def at(line: int) -> str:
        return f"e,{line}:3,{line}:5"

    v = {"type": "VAR", "addr": "(v)", "name": "v", "varType": "PORT", "loc": at(3)}
    k = {"type": "VAR", "addr": "(k)", "name": "k", "varType": "PORT", "loc": at(3)}
    read = {
        "type": "SELBIT",
        "loc": at(4),
        "fromp": [{"type": "VARREF", "varp": "(v)", "name": "v", "loc": at(4)}],
        "bitp": [{"type": "VARREF", "varp": "(k)", "name": "k", "loc": at(4)}],
    }
    func = {"type": "FUNC", "addr": "(F)", "name": "pick", "loc": at(2), "stmtsp": [v, k, read]}
    args = [
        {"type": "ARG", "exprp": [{"type": "CONST", "name": n, "loc": at(7)}]}
        for n in ("4'ha", "?32?sh6")
    ]
    calls = [{"type": "FUNCREF", "taskp": "(F)", "name": "pick", "loc": at(7), "argsp": args}]
    if where == "value":
        item = {
            "type": "VAR",
            "addr": "(R)",
            "name": "R",
            "varType": "LPARAM",
            "loc": at(7),
            "valuep": calls,
        }
    else:
        item = {"type": "ASSIGNW", "loc": at(7), "rhsp": calls}
    top = {
        "type": "MODULE",
        "addr": "(T)",
        "name": "tt_um_chip",
        "level": 1,
        "stmtsp": [func, item],
    }
    return json.dumps({"type": "NETLIST", "modulesp": [top]})  # fmt: skip


def real_tree() -> str:
    r = {
        "type": "VAR",
        "addr": "(R)",
        "name": "R",
        "varType": "LPARAM",
        "isParam": True,
        "loc": "e,7:3,7:5",
        "valuep": [{"type": "CONST", "name": "0.0", "loc": "e,7:9,7:12"}],
    }
    top = {"type": "MODULE", "addr": "(T)", "name": "tt_um_chip", "level": 1, "stmtsp": [r]}
    return json.dumps({"type": "NETLIST", "modulesp": [top]})  # fmt: skip


SIM_REAL = (
    SIM_TOP + 'P_0x1 .param/real "R" 1 4 7, Cr<m4000000000000000gfc2>; value=1.00000\n' + SIM_TABLE
)


def test_a_call_that_can_give_x_in_a_value_that_is_not_compared_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real is not compared, so a call that `constant_call` lets through in its value is
    refused (`equiv_icarus.uncompared`), before Yosys's elaboration."""
    simulator(monkeypatch, equiv.LintRun(0, ""), SIM_REAL)
    fake = fake_elab("module \\tt_um_chip\nend\n")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=real_tree(),
                    before=pick_tree("value"), fake=fake)  # fmt: skip
    assert got is not None and got[0] == "guard_sim_synth_differ", got
    assert "(chip.v, line 7) gives the parameter `R` a value with a call of a function" in got[1]
    assert "Compute the call in an integer localparam first" in got[1]
    assert fake.tags == []
    # the same tree without the call: a real parameter alone is no problem
    simulator(monkeypatch, equiv.LintRun(0, ""), SIM_REAL)
    got, _ = purity(tmp_path / "b", monkeypatch, equiv.LintRun(0, ""), tree=real_tree(),
                    fake=fake_elab("module \\tt_um_chip\nend\n"))  # fmt: skip
    assert got is None


def test_a_constant_call_outside_a_parameter_value_is_refused_after_the_elaboration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 15 inlcase/inlfn: `pick(4'b1010, 6)` outside a parameter value is refused
    (`equiv_tree.constant_call`), after Yosys's elaboration and its other findings."""
    fake = fake_elab("module \\tt_um_chip\nend\n")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE,
                    before=pick_tree("assign"), fake=fake)  # fmt: skip
    assert got is not None and got[0] == "guard_sim_synth_differ", got
    assert "(chip.v, line 7) calls the function `pick` with constant arguments" in got[1]
    assert fake.tags == ["elab"]
    fake = fake_elab("module \\tt_um_chip\nend\n", elab=(0, "inline", OUT_OF_RANGE))
    got, _ = purity(tmp_path / "b", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE,
                    before=pick_tree("assign"), fake=fake)  # fmt: skip
    assert got is not None and "calls the function `pick`" in got[1]  # before Yosys's warning


# -- round 19: a real that is infinite or NaN, converted where Yosys warns --------------------

REAL_INF = "chip.v:11: Warning: converting real value inf to binary 32'x.\n"


@pytest.mark.parametrize(
    ("log", "shown"),
    [
        (REAL_INF, "the RTL (chip.v, line 11) converts a real that is infinite or not a number "
                   "(Yosys: `inf`)"),
        ("fr7.v:7: Warning: converting real value inf to binary 16'x.\n", "(fr7.v, line 7)"),
        ("rc3.v:7: Warning: converting real value -nan to binary 32'x.\n", "(Yosys: `-nan`)"),
        ("a.v:3: Warning: converting real value -inf to binary 4'xxxx.\n", "(Yosys: `-inf`)"),
        ("Warning: converting real value nan to binary 8'x.\n", "the RTL converts a real"),
    ],
)  # fmt: skip
def test_an_infinite_or_nan_real_that_yosys_converts_is_named_with_its_line(
    log: str, shown: str
) -> None:
    got = equiv._yosys_real_x(f"1. Executing Verilog frontend.\n{log}End of script.\n")
    assert got is not None and shown in got, got
    assert "A simulator makes that integer all x" in got
    assert "keep its value finite" in got


def test_an_infinite_real_warning_after_a_long_elaboration_log_is_found() -> None:
    """YR8 (round 20): the warning comes wherever Yosys gets to the conversion, after several
    KB of derive and parameter lines in a design with sub-modules, and the whole log counts."""
    head = "".join(
        f"Parameter \\W = {i}\nGenerating RTLIL representation for module `$paramod\\sub\\W={i}'.\n"
        for i in range(400)
    )
    assert len(head) > 20000
    log = head + "big.v:812: Warning: converting real value inf to binary 32'x.\nEnd of script.\n"
    got = equiv._yosys_real_x(log)
    assert got is not None and "(big.v, line 812)" in got, got


def test_a_finite_real_that_yosys_converts_is_no_finding() -> None:
    finite = (
        "rw09c.v:7: Warning: converting real value 3.000000e+00 to binary 3.\n"
        "fr.v:2: Warning: converting real value 2.500000e+00 to binary 3.\n"
        "big.v:2: Warning: converting real value 1.000000e+30 to binary 32'10010110.\n"
    )
    assert equiv._yosys_real_x(finite) is None
    assert equiv._yosys_real_x(OUT_OF_RANGE) is None
    assert equiv._yosys_real_x("") is None


def test_a_real_that_is_x_once_yosys_converts_it_is_refused_after_the_constant_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 19 fr1/fr10/rw09/rw10: `v = R;` in a function, `w <= R;`, `wire [3:0] w =
    4'(integer'(R));` with `R = 1.0e999`: Yosys warns that the real comes out all x. Named
    after a constant call and before a select outside the range (a more general finding)."""
    module_il = "module \\tt_um_chip\nend\n"
    fake = fake_elab(module_il, elab=(0, "inline", REAL_INF))
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is not None and got[0] == "guard_sim_synth_differ", got
    assert got[1].startswith("the RTL (chip.v, line 11) converts a real that is infinite"), got
    fake = fake_elab(module_il, elab=(0, "inline", OUT_OF_RANGE + REAL_INF))
    got, _ = purity(tmp_path / "b", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is not None and "converts a real that is infinite" in got[1]
    fake = fake_elab(module_il, elab=(0, "inline", REAL_INF))
    got, _ = purity(tmp_path / "c", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE,
                    before=pick_tree("assign"), fake=fake)  # fmt: skip
    assert got is not None and "calls the function `pick`" in got[1]  # the constant call first
    fake = fake_elab(module_il, elab=(0, "inline", REAL_INF.replace("inf to binary 32'x", "1")))
    got, _ = purity(tmp_path / "d", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE, fake=fake)
    assert got is None


# `f(8'h81, M)` on line 12 of chip.v (an `assign`), where f reads `v[k]` with a 3-bit argument k
# into an 8-bit v (proven), and `localparam M` is on line 11: as Verilator's tree has it before
# V3Param. Whether M is plain depends on both tools' values.
def proven_call_tree() -> str:
    def at(line: int) -> str:
        return f"e,{line}:3,{line}:5"

    def logic(left: int) -> dict[str, Any]:
        bound = [{"type": "CONST", "name": f"?32?sh{left:x}"}]
        rng = {"type": "RANGE", "leftp": bound, "rightp": [{"type": "CONST", "name": "?32?sh0"}]}
        return {"type": "BASICDTYPE", "keyword": "logic", "rangep": [rng]}

    def port(addr: str, name: str, left: int) -> dict[str, Any]:
        return {"type": "VAR", "addr": addr, "name": name, "varType": "PORT", "loc": at(3),
                "direction": "INPUT", "isFuncLocal": True,
                "childDTypep": [logic(left)]}  # fmt: skip

    ret = {"type": "VAR", "addr": "(r)", "name": "f", "loc": at(3), "isFuncLocal": True,
           "isFuncReturn": True, "childDTypep": [logic(0)]}  # fmt: skip
    read = {
        "type": "ASSIGN",
        "loc": at(4),
        "lhsp": [{"type": "VARREF", "varp": "(r)", "name": "f", "access": "WR", "loc": at(4)}],
        "rhsp": [{
            "type": "SELBIT", "loc": at(4),
            "fromp": [{"type": "VARREF", "varp": "(v)", "name": "v", "access": "RD", "loc": at(4)}],
            "bitp": [{"type": "VARREF", "varp": "(k)", "name": "k", "access": "RD", "loc": at(4)}],
        }],
    }  # fmt: skip
    func = {"type": "FUNC", "addr": "(F)", "name": "f", "loc": at(2),
            "stmtsp": [ret, port("(v)", "v", 7), port("(k)", "k", 2), read]}  # fmt: skip
    m = {"type": "VAR", "addr": "(M)", "name": "M", "varType": "LPARAM", "isParam": True,
         "loc": "e,11:20,11:21", "valuep": [{"type": "CONST", "name": "?32?sh3"}]}  # fmt: skip
    args = [
        {"type": "ARG", "exprp": [{"type": "CONST", "name": "8'h81", "loc": at(12)}]},
        {"type": "ARG", "exprp": [{"type": "VARREF", "varp": "(M)", "name": "M", "loc": at(12)}]},
    ]
    call_f = {"type": "FUNCREF", "taskp": "(F)", "name": "f", "loc": at(12), "argsp": args}
    item = {"type": "ASSIGNW", "loc": at(12), "rhsp": [call_f]}
    top = {"type": "MODULE", "addr": "(T)", "name": "tt_um_chip", "level": 1,
           "stmtsp": [func, m, item]}  # fmt: skip
    return json.dumps({"type": "NETLIST", "modulesp": [top]})


SIM_M3 = SIM_TOP + 'P_0x1 .param/l "M" 1 4 11, +C4<00000000000000000000000000000011>;\n' + SIM_TABLE


@pytest.mark.parametrize(
    ("verilator", "vvp", "refused"),
    [
        (param_tree("32'sh3"), SIM_M3, False),  # a plain number in both tools
        # PV6: only Verilator lists M: its value is not compared, so it is not plain (the
        # simulator's values, not Verilator's twice, decide)
        (param_tree("32'sh3"), SIM_CLEAN, True),
        (EMPTY_TREE, SIM_M3, True),  # only the simulator lists it: built, not plain
        # r01/r09: neither tool builds M (an unchosen generate branch): dead code in every tool
        (EMPTY_TREE, SIM_CLEAN, False),
    ],
    ids=["plain", "verilator-only", "simulator-only", "built-by-no-tool"],
)
def test_a_proven_call_with_a_parameter_counts_its_values_in_both_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verilator: str, vvp: str, refused: bool
) -> None:
    simulator(monkeypatch, equiv.LintRun(0, ""), vvp)
    fake = fake_elab("module \\tt_um_chip\nend\n")
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=verilator,
                    before=proven_call_tree(), fake=fake)  # fmt: skip
    if refused:
        assert got is not None and got[0] == "guard_sim_synth_differ", got
        assert "(chip.v, line 12) calls the function `f` with constant arguments" in got[1]
    else:
        assert got is None, got
    assert fake.tags == ["elab"]


def test_a_simulator_that_times_out_or_cannot_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    simulator(monkeypatch, equiv.LintRun(-1, "", True), None)
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE)
    assert got is not None and got[0] == "time_limit" and "(Icarus) did not compile" in got[1]
    simulator(monkeypatch, None, None)
    with caplog.at_level("WARNING"):
        got, _ = purity(
            tmp_path / "b", monkeypatch, equiv.LintRun(0, ""), tree=EMPTY_TREE,
            fake=fake_elab("module \\tt_um_chip\nend\n"),
        )  # fmt: skip
    assert got is None and "iverilog could not be started" in caplog.text


def test_the_run_icarus_helper_starts_iverilog(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    def run(argv: list[str], **kw: Any) -> Any:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, "out", "err")

    monkeypatch.setattr(equiv.subprocess, "run", run)
    monkeypatch.setattr(equiv, "_run_icarus", REAL_RUN_ICARUS)
    got = equiv._run_icarus(["iverilog", "-V"], Path("."))
    assert got == equiv.LintRun(0, "err\nout") and seen == [["iverilog", "-V"]]


# -- round 14: the referee and the k-induction re-check -----------------------------------------


def test_the_referee_compares_the_rtl_without_its_initial_values(tmp_path: Path) -> None:
    """Stage 2 showed the initial values change nothing after reset; an `init` in the referee
    would fix a power-up value that the RTL's x power-up must leave open."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_good", "equiv.log"),
        base=(0, "counter_good", "base.log"),
        bmc=(0, "counter_good", "bmc.log"),
    )
    assert go(tmp_path, fake)["verdict"] == "equivalent"
    model = (tmp_path / "scratch" / "bmc_model.ys").read_text()
    noinit = equiv_script.stash(equiv_script.gold("chip", "rtl_noinit"), "chip", "gold")
    assert noinit in model and equiv_script.gold("chip", "rtl") not in model


def test_a_clean_recheck_is_never_a_pass_even_when_the_zero_start_induction_closes(
    tmp_path: Path,
) -> None:
    """Round 13 n23v shape, with a `kind_zero` that closes: the x-only difference of `kind_x` is
    not confirmed by the per-side search (bounded), so `kind_x` proved nothing, and the
    zero-start induction alone covers only the all-zero power-up: not proven."""
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),  # uo_out[5]: RTL 0, netlist x
        kind_x_check=(0, "counter_good", "bmc.log"),  # the per-side search: no difference
        kind_zero=(0, "shiftnr_m0165", "kind_x.log"),  # closes
    )
    got = go(tmp_path, fake)
    assert fake.tags == [
        "equiv",
        "kind_x",
        "kind_x_check_model",
        "kind_x_check_once",
        "kind_x_check",
        "kind_zero",
    ]
    assert got["verdict"] == "not_proven" and got["reason"] == "unproven_points"
    assert "no difference was found in the first 16 cycles from reset" in got["message"]


def test_a_recheck_that_fails_is_an_error_and_ends_the_check(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),
        kind_x_check=(1, "inline", "ERROR: boom\n"),
        kind_zero=(0, "shiftnr_m0165", "kind_x.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "error" and got["ok"] is False
    assert fake.tags == [
        "equiv",
        "kind_x",
        "kind_x_check_model",
        "kind_x_check_once",
        "kind_x_check",
    ]
    assert got["message"].startswith("Could not run the check from reset")
