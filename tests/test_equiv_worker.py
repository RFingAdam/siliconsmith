# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The equivalence check's verdicts, with a fake Yosys that answers with saved real logs.

Each test names the scenario whose real logs it replays (see tests/fixtures/equiv/README.md) and
asserts the verdict the check must reach. Whatever the verdict, `ok` is true only for
`equivalent`: a guard refusal, a time limit, a power-up-dependent difference and a Yosys failure
are never a pass.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import equiv

FIX = REPO_ROOT / "tests" / "fixtures" / "equiv"
NOT_A_PASS = "This is NOT a pass."


class FakeYosys:
    """Stands in for `equiv.run_yosys`: each tag gets (exit code, scenario, log file)."""

    def __init__(self, model: str, **answers: tuple[int | None, str, str] | None) -> None:
        self.model = model  # the scenario whose equiv.json / equiv.il stage 1 "writes"
        self.answers = answers
        self.calls: list[tuple[str, float]] = []

    def __call__(self, cwd: Path, script: str, tag: str, timeout: float) -> tuple[int | None, str]:
        self.calls.append((tag, timeout))
        (cwd / f"{tag}.ys").write_text(script)
        if timeout <= 0:  # like the real runner: no time left, nothing runs
            return None, ""
        answer = self.answers.get(tag)
        if answer is None:
            raise AssertionError(f"unexpected Yosys call {tag!r}; calls so far: {self.calls}")
        code, scenario, name = answer
        if tag == "equiv" and self.model:
            for f in ("equiv.json", "equiv.il"):
                shutil.copy(FIX / self.model / f, cwd / f)
        if scenario == "inline":  # a made-up one-line log, for failures with no saved log
            return code, name
        return code, (FIX / scenario / name).read_text()

    @property
    def tags(self) -> list[str]:
        return [t for t, _ in self.calls]


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
    assert fake.tags == ["equiv", "base", "bmc"]
    assert set(got["seconds"]) == {"stage1", "base", "stage4", "total"}
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
    assert {p.name for p in scratch.glob("*.ys")} == {"equiv.ys", "base.ys", "bmc.ys"}
    assert (scratch / "netlist.v").read_text() == "module tt_um_chip(); endmodule\n"
    assert (scratch / "project.v").is_file() and (scratch / "chip.v").is_file()
    assert (scratch / "base.il").is_file()
    # The scripts the check ran are the ones of equiv_script.
    assert "equiv_simple -seq 5" in (scratch / "equiv.ys").read_text()
    assert "read_verilog netlist.v" in (scratch / "equiv.ys").read_text()


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
    assert {"signal": "uio_oe[0]", "rtl": "0", "netlist": "x"} in mismatch["differs"]
    assert "cycle 1 (power-up, with rst_n low)" in got["message"]
    assert "uio_oe[0] (RTL 0, netlist x)" in got["message"]
    assert "reset does not set" in got["message"]
    row = got["trace"][0]
    assert row["cycle"] == 1 and row["inputs"]["rst_n"] == "0"
    assert row["rtl"]["uio_oe[0]"] == "0" and row["netlist"]["uio_oe[0]"] == "x"
    assert all(k.startswith(("uo_out", "uio_out", "uio_oe")) for k in row["rtl"])  # pins only


def test_a_difference_found_from_reset_names_the_bit_the_cycle_and_the_trace(
    tmp_path: Path,
) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "counter_m0115", "equiv.log"),
        kind_x=(0, "counter_m0115", "kind_x.log"),
        kind_zero=None,
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["reason"] is None
    assert fake.tags == ["equiv", "kind_x"]  # the counterexample ended it
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


def test_the_rtl_relying_on_initial_values_is_not_equivalent(tmp_path: Path) -> None:
    fake = FakeYosys(
        "counter_good",
        equiv=(0, "initz", "equiv.log"),
        initdep=(0, "initz", "initdep.log"),
    )
    got = go(tmp_path, fake)
    assert got["verdict"] == "not_equivalent" and got["reason"] == "rtl_initial_value"
    assert fake.tags == ["equiv", "initdep"]
    assert "Reset those registers with rst_n" in got["message"]
    assert "initial values" in got["message"] and got["first_mismatch"]["cycle"] >= 1


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
