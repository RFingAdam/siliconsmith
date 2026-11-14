# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The equivalence check's verdicts, with a fake Yosys that answers with saved real logs.

Each test names the scenario whose real logs it replays (see tests/fixtures/equiv/README.md) and
asserts the verdict the check must reach. Whatever the verdict, `ok` is true only for
`equivalent`: a guard refusal, a time limit, a power-up-dependent difference and a Yosys failure
are never a pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT

from siliconsmith.worker import equiv

REAL_RUN_VERILATOR = equiv._run_verilator  # the autouse fixture replaces it

FIX = REPO_ROOT / "tests" / "fixtures" / "equiv"
FIXTURES_MYHDL = REPO_ROOT / "tests" / "fixtures" / "equiv_myhdl"
NOT_A_PASS = "This is NOT a pass."


class FakeYosys:
    """Stands in for `equiv.run_yosys`: each tag gets (exit code, scenario, log file)."""

    def __init__(self, model: str, **answers: tuple[int | None, str, str] | None) -> None:
        self.model = model  # the scenario whose equiv.json / equiv.il stage 1 "writes"
        self.answers = answers
        self.calls: list[tuple[str, float]] = []
        self.gate_read = "module \\tt_um_chip\n  wire width 1 \\a\n  connect \\a 1'0\nend\n"

    def __call__(self, cwd: Path, script: str, tag: str, timeout: float) -> tuple[int | None, str]:
        self.calls.append((tag, timeout))
        (cwd / f"{tag}.ys").write_text(script)
        if timeout <= 0:  # like the real runner: no time left, nothing runs
            return None, ""
        answer = self.answers.get(tag)
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
    """The ALWCOMBORDER pass runs Verilator; unit tests do not have it (it is inconclusive)."""
    monkeypatch.setattr(equiv, "_run_verilator", lambda argv, cwd: None)  # cannot start


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
    assert got["verdict"] == "equivalent" and fake.tags == ["equiv", "initstate", "base", "bmc"]
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


def answer_tree(argv: list[str], cwd: Path) -> equiv.LintRun | None:
    """The `--json-only` run of the purity check, for a fake Verilator: the tree of star_core."""
    if "--json-only" not in argv:
        return None
    (cwd / "obj_dir").mkdir()
    (cwd / "obj_dir" / "Vtt_um_chip.tree.json").write_text(json.dumps(STAR_TREE))
    (cwd / "obj_dir" / "Vtt_um_chip.tree.meta.json").write_text(TREE_META)
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
    return equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=FakeYosys(""))


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
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "chip.v, line 4" in got["message"] and "`t`" in got["message"]
    assert "reads a signal before it writes it" in got["message"] and fake.calls == []
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
) -> tuple[tuple[str, str] | None, list[list[str]]]:
    """`equiv._function_purity` with a fake Verilator that answers `run` and writes `tree`."""
    argvs: list[list[str]] = []

    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun | None:
        argvs.append(argv)
        if tree is not None:
            out = cwd / "obj_dir"
            out.mkdir()
            (out / "Vtt_um_chip.tree.json").write_text(tree)
            (out / "Vtt_um_chip.tree.meta.json").write_text(TREE_META)
        return run

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    sources = [("project.v", "// wrapper\n"), ("chip.v", text)]
    return equiv._function_purity(tmp_path / "s", sources, "chip"), argvs


def test_no_function_task_or_star_block_means_no_purity_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "module tt_um_chip();\n  // a function in a comment\n  wire a;\nendmodule\n"
    got, argvs = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), text=text)
    assert got is None and argvs == []


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
    assert argv[-2:] == ["project.v", "chip.v"] and "tt_um_chip" in argv


def test_a_pure_tree_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = {
        "type": "NETLIST",
        "modulesp": [{"type": "FUNC", "addr": "(F)", "name": "rd", "loc": "e,3:28,3:30"}],
    }
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree))
    assert got is None


def test_a_function_missing_from_verilators_tree_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A generate branch Verilator drops (a constant `1/0` is x to Yosys, 0 to Verilator)."""
    tree = {"type": "NETLIST", "modulesp": []}
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree))
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "chip.v declares 1 function or task(s)" in got[1] and "holds only 0" in got[1]


def test_an_always_star_block_missing_from_verilators_tree_is_not_proven(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "module tt_um_chip();\n  always @* begin\n    y = t;\n  end\nendmodule\n"
    tree = {"type": "NETLIST", "modulesp": []}
    got, _ = purity(tmp_path, monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree), text=text)
    assert got is not None and got[0] == "guard_sim_synth_differ"
    assert "chip.v declares 1 `always @*` block(s)" in got[1]
    tree["modulesp"] = [{"type": "ALWAYS", "keyword": "always", "loc": "e,2:3,2:9"}]
    got, _ = purity(
        tmp_path / "x", monkeypatch, equiv.LintRun(0, ""), tree=json.dumps(tree), text=text
    )
    assert got is None


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
    assert got[1].startswith("the check could not read the functions in the RTL with Verilator: ")
    assert shown in got[1]


def test_a_purity_run_that_cannot_start_is_inconclusive_and_logged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("WARNING"):
        got, _ = purity(tmp_path, monkeypatch, None)
    assert got is None and "function-purity check was skipped" in caplog.text


def test_check_refuses_an_impure_function_before_any_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def verilator(argv: list[str], cwd: Path) -> equiv.LintRun:
        (cwd / "obj_dir").mkdir()
        (cwd / "obj_dir" / "Vtt_um_chip.tree.json").write_text(json.dumps(IMPURE_TREE))
        (cwd / "obj_dir" / "Vtt_um_chip.tree.meta.json").write_text(TREE_META)
        return equiv.LintRun(0, "")

    monkeypatch.setattr(equiv, "_run_verilator", verilator)
    rtl, netlist = inputs(tmp_path)
    (rtl / "chip.v").write_text(FUNC_RTL)
    fake = FakeYosys("")
    got = equiv.check(rtl, "chip", netlist, tmp_path / "s", yosys=fake)
    assert got["verdict"] == "not_proven" and got["reason"] == "guard_sim_synth_differ"
    assert "the function `rd` (chip.v, line 3) reads `cnt` (line 4)" in got["message"]
    assert fake.calls == []
