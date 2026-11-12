# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The validation ledger and derived status, with tmp projects and no Docker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

import siliconsmith.ledger as ledger_mod
from siliconsmith.ledger import (
    CORRUPT_REL,
    LEDGER_REL,
    MAX_RUNS,
    Approval,
    RunRecord,
    RunTest,
    append_run,
    compute_digests,
    load_ledger,
    set_approval,
)
from siliconsmith.project import Project
from siliconsmith.result import ToolResult
from siliconsmith.scaffold import create_project
from siliconsmith.server import build_server
from siliconsmith.status import DIGITAL_STEPS, compute_status, refresh_status
from siliconsmith.tools.status import build_result
from siliconsmith.tt_sync import sync_spec_files

IMAGE = "example/toolchain:ctx-aaaa"
SPEC = """\
title: Chip
summary: A test chip.
clock_hz: 10000000
reset: "rst_n, active-low, synchronous"
requirements:
  - id: R1
    text: First thing.
  - id: R2
    text: Second thing.
"""
STEP_KEYS = [s.key for s in DIGITAL_STEPS]


def make_project(tmp_path: Path, frontend: str = "amaranth") -> Project:
    project, _ = create_project(tmp_path, "chip", frontend, "Ada Lovelace", "A chip.")
    (project.dir / "spec.yaml").write_text(SPEC)
    assert sync_spec_files(project).ok  # as write_spec does after a valid save
    return project


def rows(r1: str = "R1", r2: str = "R2") -> list[RunTest]:
    return [
        RunTest(name="test_a", module="test", covers=[r1], outcome="pass"),
        RunTest(name="test_b", module="test", covers=[r2], outcome="pass"),
    ]


def add_run(
    project: Project,
    tool: str = "run_tests",
    sim: str | None = "icarus",
    *,
    tests: list[RunTest] | None = None,
    ok: bool = True,
    stage: str = "sim",
    filtered: bool = False,
    image: str = IMAGE,
    gl: bool = False,
) -> None:
    append_run(
        project.dir,
        RunRecord(
            tool=tool,  # type: ignore[arg-type]
            sim=sim,  # type: ignore[arg-type]
            at="2026-11-12T14:03:05Z",
            ok=ok,
            stage=stage,
            filtered=filtered,
            gl=gl,
            image=image,
            siliconsmith_version=ledger_mod.__version__,
            digests=compute_digests(project, image),
            tests=rows() if tests is None else tests,
        ),
    )


def approve(project: Project) -> None:
    sha = hashlib.sha256((project.dir / "spec.yaml").read_bytes()).hexdigest()
    set_approval(
        project.dir, Approval(spec_sha256=sha, approved_by="Ada", at="2026-11-12T14:00:00Z")
    )


def all_pass(project: Project) -> None:
    approve(project)
    add_run(project, "emit_verilog", None, tests=[], stage="done")
    add_run(project, sim="icarus")
    add_run(project, sim="verilator")


def states(project: Project, image: str = IMAGE) -> dict[str, str]:
    return {s.key: s.state for s in compute_status(project, image).steps}


def stale_set(project: Project, image: str = IMAGE) -> set[str]:
    now = states(project, image)
    return {k for k, v in now.items() if v == "stale"}


# -- the plain picture ---------------------------------------------------------------------


def test_everything_passes_after_full_runs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    report = compute_status(project, IMAGE)
    assert {s.key: s.state for s in report.steps} == {
        "spec_valid": "pass",
        "spec_approved": "pass",
        "check": "pass",
        "sim_icarus": "pass",
        "sim_verilator": "pass",
        "coverage": "pass",
        "synthesis": "not_run",
        "gate_sim": "not_run",
        "equivalence": "not_available",
        "layout": "not_available",
        "tt_precheck": "not_available",
        "bring_up": "not_available",
    }
    by_key = {s.key: s for s in report.steps}
    assert by_key["sim_icarus"].evidence == "run_tests icarus: 2/2 passed"
    assert by_key["synthesis"].evidence.startswith("Call synthesize")
    assert by_key["gate_sim"].evidence.startswith(
        "Call run_tests with simulator icarus and gl=True"
    )
    assert by_key["equivalence"].evidence == "Comes with milestone M1"
    assert by_key["layout"].evidence == "Comes with milestone M2"
    assert by_key["tt_precheck"].evidence == "Comes with milestone M3"
    assert by_key["bring_up"].evidence == "Comes with milestone M3"
    assert by_key["sim_icarus"].date == "2026-11-12 14:03 UTC"


def test_new_project_is_not_run_everywhere(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    got = states(project)
    assert got["spec_valid"] == "pass"
    for key in ("spec_approved", "check", "sim_icarus", "sim_verilator", "coverage"):
        assert got[key] == "not_run"
    assert got["synthesis"] == "not_run" and got["gate_sim"] == "not_run"
    assert [got[k] for k in STEP_KEYS[8:]] == ["not_available"] * 4


def test_invalid_and_missing_spec(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project.dir / "spec.yaml").write_text("title: x\n")
    got = compute_status(project, IMAGE)
    assert got.steps[0].state == "fail" and "required field is missing" in got.steps[0].evidence
    (project.dir / "spec.yaml").unlink()
    assert states(project)["spec_valid"] == "not_run"
    assert states(project)["coverage"] == "not_run"


# -- acceptance: each step goes stale only when its own inputs change ----------------------

CHECK_SIM_COV = {"check", "sim_icarus", "sim_verilator", "coverage"}
SIM_COV = {"sim_icarus", "sim_verilator", "coverage"}


def edit(rel: str, text: str = "# changed\n") -> Any:
    def do(project: Project) -> None:
        path = project.dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text() if path.exists() else ""
        path.write_text(old + text)

    return do


@pytest.mark.parametrize(
    ("frontend", "change", "expected"),
    [
        ("amaranth", edit("spec.yaml"), {"spec_approved", "coverage"}),
        ("amaranth", edit("hdl/chip.py"), CHECK_SIM_COV),
        ("amaranth", edit("hdl/sub/helper.py"), CHECK_SIM_COV),
        ("myhdl", edit("hdl/chip.py"), CHECK_SIM_COV),
        ("verilog", edit("src/chip.v", "// changed\n"), CHECK_SIM_COV),
        ("amaranth", edit("src/chip.v", "// generated\n"), set()),
        ("amaranth", edit("src/project.v", "// generated\n"), set()),
        ("amaranth", edit("info.yaml", "# changed\n"), set()),
        ("amaranth", edit("test/tb.v", "// changed\n"), CHECK_SIM_COV),
        ("amaranth", edit("test/test.py"), SIM_COV),
        ("amaranth", edit("test/sub/x.py"), SIM_COV),
        ("amaranth", edit("docs/info.md", "more words\n"), set()),
        ("amaranth", edit("docs/verification.md", "hand edit\n"), set()),
        ("amaranth", edit("build/out.txt"), set()),
        ("amaranth", edit("build/sub/x.py"), set()),
        ("amaranth", edit("test/__pycache__/x.pyc", "cache"), set()),
        ("amaranth", edit("test/helpers/x.py"), SIM_COV),
        ("amaranth", edit("test/results.xml", "<x/>"), set()),
    ],
    ids=lambda v: v if isinstance(v, str) and not v.startswith("<") else "",
)
def test_each_input_class_makes_only_its_steps_stale(
    tmp_path: Path, frontend: str, change: Any, expected: set[str]
) -> None:
    project = make_project(tmp_path, frontend)
    all_pass(project)
    assert stale_set(project) == set()
    change(project)
    assert stale_set(project) == expected
    # What is not stale still passes.
    now = states(project)
    for key in STEP_KEYS[:6]:
        assert now[key] in ("pass", "stale"), (key, now[key])


# -- synthesis: its inputs are the check inputs plus src/config.json and info.yaml ---------


def add_synth(project: Project, *, ok: bool = True, stage: str = "synth") -> None:
    add_run(project, "synthesize", None, tests=[], ok=ok, stage=stage)


def add_gl(
    project: Project,
    *,
    ok: bool = True,
    stage: str = "sim",
    tests: list[RunTest] | None = None,
    filtered: bool = False,
) -> None:
    add_run(project, sim="icarus", gl=True, ok=ok, stage=stage, tests=tests, filtered=filtered)


GL = {"gate_sim"}


@pytest.mark.parametrize(
    ("frontend", "change", "expected"),
    [
        ("amaranth", edit("src/config.json", "\n"), {"synthesis"} | GL),
        ("amaranth", edit("info.yaml", "# changed\n"), {"synthesis"} | GL),
        ("amaranth", edit("hdl/chip.py"), CHECK_SIM_COV | {"synthesis"} | GL),
        ("verilog", edit("src/chip.v", "// changed\n"), CHECK_SIM_COV | {"synthesis"} | GL),
        ("amaranth", edit("test/tb.v", "// changed\n"), CHECK_SIM_COV | {"synthesis"} | GL),
        ("amaranth", edit("test/test.py"), SIM_COV | GL),
        ("amaranth", edit("spec.yaml"), {"spec_approved", "coverage"}),
        ("amaranth", edit("docs/info.md", "more\n"), set()),
        ("amaranth", edit("build/out.txt"), set()),
    ],
    ids=lambda v: v if isinstance(v, str) and not v.startswith("<") else "",
)
def test_synthesis_and_gate_sim_stale_matrix(
    tmp_path: Path, frontend: str, change: Any, expected: set[str]
) -> None:
    project = make_project(tmp_path, frontend)
    all_pass(project)
    add_synth(project)
    add_gl(project)
    assert states(project)["synthesis"] == "pass" and states(project)["gate_sim"] == "pass"
    assert stale_set(project) == set()
    change(project)
    assert stale_set(project) == expected


def test_hand_edited_clock_or_tiles_make_synthesis_stale(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_synth(project)
    assert states(project)["synthesis"] == "pass"
    spec = project.dir / "spec.yaml"
    original = spec.read_text()
    spec.write_text(original + "tiles: 2x2\n")
    assert states(project)["synthesis"] == "stale"
    spec.write_text(original)
    assert states(project)["synthesis"] == "pass"
    assert sync_spec_files(project).ok
    add_synth(project)
    spec.write_text(original.replace("clock_hz: 10000000", "clock_hz: 50000000"))
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["synthesis"].state == "stale"
    assert "call synthesize" in got["synthesis"].evidence


def test_synthesis_image_makes_it_stale(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    add_synth(project)
    add_gl(project)
    assert stale_set(project, "example/toolchain:ctx-bbbb") == CHECK_SIM_COV | {"synthesis"} | GL


def test_synthesis_states(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert states(project)["synthesis"] == "not_run"
    add_synth(project, ok=False, stage="too_big")
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["synthesis"].state == "fail"
    assert "too big" in got["synthesis"].evidence
    add_synth(project)  # the latest synthesize record decides
    assert states(project)["synthesis"] == "pass"
    edit("src/config.json", "\n")(project)
    assert states(project)["synthesis"] == "stale"
    add_synth(project, ok=False, stage="lint")
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["synthesis"].state == "fail" and "lint" in got["synthesis"].evidence


def test_synthesis_shows_facts_and_a_synth_record_is_check_evidence(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    append_run(
        project.dir,
        RunRecord(
            tool="synthesize",
            at="2026-11-12T14:03:05Z",
            ok=True,
            stage="synth",
            image=IMAGE,
            siliconsmith_version=ledger_mod.__version__,
            digests=compute_digests(project, IMAGE),
            synth=ledger_mod.SynthFacts(
                cells=120, area_um2=912.4, utilization=0.0922, tiles="1x1", verdict="ok"
            ),
        ),
    )
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["synthesis"].evidence == (
        "synthesize: 120 cells, 912 um2, about 9% of the 1x1 cell budget (estimate)"
    )
    assert got["check"].state == "pass"  # lint was clean for synthesis to run


def test_synthesis_does_not_touch_other_steps(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    before = states(project)
    add_synth(project, ok=False, stage="synth_failed")
    after = states(project)
    assert {k: v for k, v in after.items() if k != "synthesis"} == {
        k: v for k, v in before.items() if k != "synthesis"
    }
    assert after["synthesis"] == "fail"


def test_old_ledger_without_synth_digest_still_loads(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    path = project.dir / LEDGER_REL
    data = json.loads(path.read_text())
    for run in data["runs"]:
        del run["digests"]["synth"]
    path.write_text(json.dumps(data))
    assert load_ledger(project.dir).problem is None
    assert states(project)["sim_icarus"] == "pass"
    assert states(project)["synthesis"] == "not_run"
    # An M0 record is never read as a synthesis result, and a missing digest is never current.
    add_run(project, "synthesize", None, tests=[], stage="synth")
    data = json.loads(path.read_text())
    del data["runs"][-1]["digests"]["synth"]
    path.write_text(json.dumps(data))
    assert states(project)["synthesis"] == "stale"


def test_image_name_makes_check_sims_and_coverage_stale(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    assert stale_set(project, "example/toolchain:ctx-bbbb") == CHECK_SIM_COV
    assert states(project, "example/toolchain:ctx-bbbb")["spec_approved"] == "pass"


def test_siliconsmith_version_makes_check_sims_and_coverage_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    monkeypatch.setattr(ledger_mod, "__version__", "9.9.9")
    assert stale_set(project) == CHECK_SIM_COV


def test_rerunning_clears_staleness(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    edit("test/sub/x.py")(project)
    add_run(project, sim="icarus")
    add_run(project, sim="verilator")
    assert stale_set(project) == set()


# -- acceptance: simulation and coverage are set only by unfiltered runs with tests --------


def test_filtered_run_sets_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, sim="icarus", filtered=True)
    add_run(project, sim="verilator", filtered=True)
    got = states(project)
    assert got["sim_icarus"] == got["sim_verilator"] == got["coverage"] == "not_run"


def test_filtered_run_changes_nothing_after_full_runs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    before = states(project)
    add_run(project, sim="icarus", filtered=True, ok=False, tests=rows()[:1])
    assert states(project) == before


def test_zero_test_run_sets_nothing_and_does_not_replace_a_pass(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, sim="icarus", tests=[], ok=False)
    assert states(project)["sim_icarus"] == "not_run"
    all_pass(project)
    add_run(project, sim="icarus", tests=[], ok=False)
    assert states(project)["sim_icarus"] == "pass"
    assert states(project)["coverage"] == "pass"


def test_only_skipped_tests_set_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    skipped = [RunTest(name="t", module="test", covers=["R1"], outcome="skip")]
    add_run(project, sim="icarus", tests=skipped, ok=False)
    assert states(project)["sim_icarus"] == "not_run"


def test_one_simulator_only_is_not_coverage(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, sim="icarus")
    got = compute_status(project, IMAGE)
    cov = got.steps[5]
    assert [s.state for s in got.steps[3:6]] == ["pass", "not_run", "not_run"]
    assert "Verilator" in cov.evidence


def test_stale_second_simulator_is_stale_coverage(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, sim="verilator")
    edit("test/test.py")(project)
    add_run(project, sim="icarus")
    got = states(project)
    assert got["sim_icarus"] == "pass"
    assert got["sim_verilator"] == "stale"
    assert got["coverage"] == "stale"


def test_expect_fail_test_never_covers(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    tests = [
        RunTest(name="test_a", module="test", covers=["R1"], outcome="pass"),
        # run_tests records covers=[] for an expect_fail test (it covers nothing).
        RunTest(name="test_b", module="test", covers=[], outcome="pass"),
    ]
    add_run(project, sim="icarus", tests=tests)
    add_run(project, sim="verilator", tests=tests)
    got = compute_status(project, IMAGE)
    assert got.steps[5].state == "fail"
    assert got.steps[5].evidence.startswith("R2 not covered")
    assert got.steps[3].state == got.steps[4].state == "pass"


def test_skipped_test_next_to_a_pass_does_not_cover(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    tests = [
        RunTest(name="test_a", module="test", covers=["R1"], outcome="pass"),
        RunTest(name="test_b", module="test", covers=["R2"], outcome="skip"),
    ]
    add_run(project, sim="icarus", tests=tests)
    add_run(project, sim="verilator", tests=tests)
    got = compute_status(project, IMAGE)
    assert got.steps[3].evidence == "run_tests icarus: 1/1 passed"
    assert got.steps[5].state == "fail" and "R2" in got.steps[5].evidence


def test_a_requirement_must_pass_on_both_simulators(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, sim="icarus")
    # On Verilator R2's test failed, though another test covers R1 and the run is "ok".
    tests = [
        RunTest(name="test_a", module="test", covers=["R1"], outcome="pass"),
        RunTest(name="test_b", module="test", covers=["R2"], outcome="fail"),
    ]
    add_run(project, sim="verilator", tests=tests, ok=False)
    got = compute_status(project, IMAGE)
    assert got.steps[4].state == "fail"
    assert "test_b" in got.steps[4].evidence
    assert got.steps[5].state == "fail"


def test_failed_check_and_later_pass(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, "emit_verilog", None, tests=[], ok=False, stage="lint")
    got = compute_status(project, IMAGE)
    assert got.steps[2].state == "fail" and "lint" in got.steps[2].evidence
    add_run(project, "run_tests", "icarus", stage="sim")  # reached the simulator: lint was clean
    assert states(project)["check"] == "pass"


def test_timeouts_do_not_decide_the_check(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    add_run(project, "emit_verilog", None, tests=[], stage="done")
    add_run(project, "run_tests", "icarus", tests=[], ok=False, stage="timeout")
    assert states(project)["check"] == "pass"


def test_spec_change_after_approval_is_stale(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert states(project)["spec_approved"] == "not_run"
    approve(project)
    assert states(project)["spec_approved"] == "pass"
    (project.dir / "spec.yaml").write_text(SPEC + "open_questions:\n  - Is it fast?\n")
    assert states(project)["spec_approved"] == "stale"


# -- the ledger file -----------------------------------------------------------------------


def test_missing_ledger_is_empty_without_a_problem(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    loaded = load_ledger(project.dir)
    assert loaded.problem is None and loaded.ledger.runs == [] and loaded.ledger.approval is None


@pytest.mark.parametrize("junk", [b"{not json", b"[]", b'{"version": 2}', b"\xff\xfe", b""])
def test_corrupt_ledger_is_not_fatal(tmp_path: Path, junk: bytes) -> None:
    project = make_project(tmp_path)
    (project.dir / ".siliconsmith").mkdir(exist_ok=True)
    (project.dir / LEDGER_REL).write_bytes(junk)
    report = compute_status(project, IMAGE)
    assert report.ledger_problem
    assert [s.state for s in report.steps[1:6]] == ["not_run"] * 5
    assert any("treating it as empty" in line for line in report.summary_lines())
    # The next append keeps the bad file aside and starts fresh.
    add_run(project, "emit_verilog", None, tests=[], stage="done")
    assert (project.dir / CORRUPT_REL).read_bytes() == junk
    loaded = load_ledger(project.dir)
    assert loaded.problem is None and len(loaded.ledger.runs) == 1
    assert states(project)["check"] == "pass"


def test_ledger_that_is_a_symlink_is_not_fatal(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project.dir / ".siliconsmith").mkdir(exist_ok=True)
    (project.dir / LEDGER_REL).symlink_to(project.dir / "spec.yaml")
    assert compute_status(project, IMAGE).ledger_problem


def test_ledger_keeps_the_last_200_runs(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    for _ in range(MAX_RUNS + 5):
        add_run(project, "emit_verilog", None, tests=[], stage="done")
    assert len(load_ledger(project.dir).ledger.runs) == MAX_RUNS


def test_ledger_stores_facts_only(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    data = json.loads((project.dir / LEDGER_REL).read_text())
    assert set(data) == {"version", "approval", "runs"}
    run = data["runs"][1]
    assert set(run) == {
        "tool",
        "sim",
        "at",
        "ok",
        "stage",
        "filtered",
        "gl",
        "image",
        "image_id",
        "siliconsmith_version",
        "digests",
        "tests",
        "synth",
    }
    assert set(run["digests"]) == {"check", "sim", "spec", "synth"}
    assert data["approval"]["approved_by"] == "Ada"
    assert "pass" not in json.dumps({k: v for k, v in data.items() if k != "runs"})


# -- recording from the tools --------------------------------------------------------------


def test_run_tests_and_emit_record_runs(tmp_path: Path) -> None:
    from test_emit_verilog import EmitRunner, make, outcome, worker_result
    from test_run_tests import TESTS_PY, sim_part, sim_result, sim_test

    from siliconsmith.tools.emit_verilog import emit
    from siliconsmith.tools.run_tests import run

    tests = [sim_test("test_adds"), sim_test("test_expected_failure"), sim_test("test_plain")]
    runner = EmitRunner(
        outcome(worker_result()),
        outcome(sim_result(sim_part(tests))),
        outcome(sim_result(sim_part(tests[:1], simulator="verilator"))),
    )
    ctx, project_dir = make(tmp_path, "amaranth", runner)
    (project_dir / "test" / "test.py").write_text(TESTS_PY)
    assert emit(ctx, "chip").ok
    assert run(ctx, "chip", "icarus", None, False).ok
    assert run(ctx, "chip", "verilator", "test_adds", False).ok
    runs = load_ledger(project_dir).ledger.runs
    assert [(r.tool, r.sim, r.stage, r.filtered, r.ok) for r in runs] == [
        ("emit_verilog", None, "done", False, True),
        ("run_tests", "icarus", "sim", False, True),
        ("run_tests", "verilator", "sim", True, True),
    ]
    assert runs[0].tests == []
    # run_tests records covers=[] for the expect_fail test.
    assert [(t.name, t.covers) for t in runs[1].tests] == [
        ("test_adds", ["R1"]),
        ("test_expected_failure", []),
        ("test_plain", []),
    ]
    assert runs[1].image == ctx.image and runs[1].siliconsmith_version == ledger_mod.__version__
    project = Project(project_dir, "chip", "amaranth")
    got = states(project, ctx.image)
    assert got["check"] == "pass" and got["sim_icarus"] == "pass"
    assert got["sim_verilator"] == "not_run"  # the verilator run was filtered


def test_failed_runs_are_recorded_too(tmp_path: Path) -> None:
    from test_emit_verilog import EmitRunner, make, outcome, worker_result

    from siliconsmith.tools.emit_verilog import emit

    lint = {
        "severity": "error",
        "code": "WIDTH",
        "file": "src/chip.v",
        "line": 3,
        "col": 1,
        "msg": "bad",
    }
    runner = EmitRunner(outcome(worker_result(ok=False, stage="lint", lint=[lint], lint_total=1)))
    ctx, project_dir = make(tmp_path, "amaranth", runner)
    assert not emit(ctx, "chip").ok
    ((record,),) = [load_ledger(project_dir).ledger.runs]
    assert (record.stage, record.ok) == ("lint", False)
    assert states(Project(project_dir, "chip", "amaranth"), ctx.image)["check"] == "fail"


def test_ledger_trouble_never_turns_a_result_into_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_emit_verilog import EmitRunner, make, outcome, worker_result

    from siliconsmith.tools.emit_verilog import emit

    def boom(*_: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(ledger_mod, "append_run", boom)
    ctx, _ = make(tmp_path, "amaranth", EmitRunner(outcome(worker_result())))
    result = emit(ctx, "chip")
    assert result.ok
    assert "validation ledger could not be updated" in result.summary


# -- the status tool, the command line and the generated document ---------------------------


async def call_status(root: Path, image: str = IMAGE) -> dict[str, Any]:
    async with Client(build_server(root, None, image=image)) as client:
        result = await client.call_tool("status", {"project": "chip"})
    assert not result.is_error, result.content
    assert result.structured_content is not None
    out: dict[str, Any] = result.structured_content
    return out


@pytest.mark.anyio
async def test_tool_and_command_line_give_the_same_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from siliconsmith.cli import main

    project = make_project(tmp_path)
    all_pass(project)
    monkeypatch.setenv("SILICONSMITH_IMAGE", IMAGE)
    out = await call_status(tmp_path)
    doc = (project.dir / "docs" / "verification.md").read_text()
    assert main(["status", str(project.dir)]) == 0
    printed = capsys.readouterr().out
    assert printed == out["summary"] + "\n"
    assert (project.dir / "docs" / "verification.md").read_text() == doc
    assert out["summary"] in doc
    assert out["ok"] is True
    assert [s["key"] for s in out["details"]["steps"]] == STEP_KEYS
    assert len(out["summary"].splitlines()) <= 20


@pytest.mark.anyio
async def test_tool_summary_fits_in_the_worst_case(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project.dir / "spec.yaml").write_text("title: " + "x" * 90 + "\n")
    long_names = [
        RunTest(name="test_" + "x" * 150, module="t", covers=[], outcome="fail") for _ in range(3)
    ]
    add_run(project, "emit_verilog", None, tests=[], ok=False, stage="elaborate")
    add_run(project, sim="icarus", tests=long_names, ok=False)
    add_run(project, sim="verilator", tests=long_names, ok=False)
    out = await call_status(tmp_path)
    assert "more lines in details" not in out["summary"]
    assert len(out["summary"].splitlines()) == 14 and len(out["summary"]) < 2000


def test_document_content(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    refresh_status(project, IMAGE)
    doc = (project.dir / "docs" / "verification.md").read_text()
    assert doc.startswith("<!-- Generated by Siliconsmith.")
    assert "| Step | What it proves | Status | Evidence | Date |" in doc
    assert "siliconsmith approve" in doc and "honest-use convention" in doc
    assert "siliconsmith status" in doc
    # Same input, same file.
    refresh_status(project, IMAGE)
    assert (project.dir / "docs" / "verification.md").read_text() == doc
    assert build_result(compute_status(project, IMAGE)).summary in doc


def test_status_next_hint_and_not_ok_on_failure(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = build_result(compute_status(project, IMAGE))
    assert result.ok and result.next_hints
    add_run(project, "emit_verilog", None, tests=[], ok=False, stage="lint")
    assert build_result(compute_status(project, IMAGE)).ok is False


def test_cli_status_rejects_a_folder_that_is_not_a_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from siliconsmith.cli import main

    assert main(["status", str(tmp_path)]) == 2
    assert "not a Siliconsmith project" in capsys.readouterr().err


def test_cli_status_with_a_corrupt_ledger_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from siliconsmith.cli import main

    project = make_project(tmp_path)
    (project.dir / ".siliconsmith").mkdir(exist_ok=True)
    (project.dir / LEDGER_REL).write_text("{")
    assert main(["status", str(project.dir)]) == 0
    out = capsys.readouterr().out
    assert "not run" in out and "treating it as empty" in out


def test_result_helper_is_a_plain_tool_result(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    assert isinstance(build_result(compute_status(project, IMAGE)), ToolResult)


# -- gate-level simulation ----------------------------------------------------------------


def test_gate_sim_states(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["gate_sim"].state == "not_run"
    add_gl(project)
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["gate_sim"].state == "pass"
    assert got["gate_sim"].evidence == "run_tests gl: 2/2 passed on the sky130 netlist"
    bad = [RunTest(name="test_a", module="test", covers=["R1"], outcome="fail"), *rows()[1:]]
    add_gl(project, ok=False, tests=bad)
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert got["gate_sim"].state == "fail" and "failed: test_a" in got["gate_sim"].evidence
    # A synthesis problem inside a gate-level run is a failure of this step too.
    add_gl(project, ok=False, stage="synth_failed", tests=[])
    got = {s.key: s for s in compute_status(project, IMAGE).steps}
    assert (
        got["gate_sim"].state == "fail" and "synthesis found a problem" in got["gate_sim"].evidence
    )
    add_gl(project, ok=False, stage="too_big", tests=[])
    assert states(project)["gate_sim"] == "fail"
    # A filtered run is never the full run, and a run that failed the emit step says nothing.
    add_gl(project, filtered=True)
    add_gl(project, ok=False, stage="lint", tests=[])
    assert states(project)["gate_sim"] == "fail"  # still the too_big run
    add_gl(project)
    assert states(project)["gate_sim"] == "pass"  # the latest full run decides


def test_gate_sim_needs_icarus_gl_runs_only(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)  # RTL runs on both simulators
    add_synth(project)
    assert states(project)["gate_sim"] == "not_run"


def test_gate_sim_is_stale_when_the_spec_clock_no_longer_matches_the_files(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    add_gl(project)
    spec = project.dir / "spec.yaml"
    spec.write_text(spec.read_text().replace("clock_hz: 10000000", "clock_hz: 50000000"))
    assert states(project)["gate_sim"] == "stale"


@pytest.mark.parametrize("ok", [True, False])
def test_a_gate_level_run_never_changes_the_rtl_rows_or_coverage(tmp_path: Path, ok: bool) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    before = {s.key: (s.state, s.evidence, s.date) for s in compute_status(project, IMAGE).steps}
    bad = [RunTest(name="test_a", module="test", covers=[], outcome="fail")]
    # Later than the RTL runs, on icarus, unfiltered: it must still be invisible to them.
    add_gl(project, ok=ok, tests=rows() if ok else bad)
    after = {s.key: (s.state, s.evidence, s.date) for s in compute_status(project, IMAGE).steps}
    for key in ("sim_icarus", "sim_verilator", "coverage", "synthesis"):
        assert after[key] == before[key], key
    # The check row may name the latest lint-clean run, but its state does not change.
    assert after["check"][0] == before["check"][0] == "pass"
    assert after["check"][1] == "run_tests gl: Verilog generated and lint clean"
    assert after["gate_sim"][0] == ("pass" if ok else "fail")


def test_ledger_without_the_gl_field_still_loads(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    all_pass(project)
    path = project.dir / LEDGER_REL
    data = json.loads(path.read_text())
    for run in data["runs"]:
        run.pop("gl", None)
    path.write_text(json.dumps(data))
    loaded = load_ledger(project.dir)
    assert loaded.problem is None and all(r.gl is False for r in loaded.ledger.runs)
    assert states(project)["sim_icarus"] == "pass" and states(project)["gate_sim"] == "not_run"
