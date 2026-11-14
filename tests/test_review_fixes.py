# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ledger size cap, run-time digests, approve cancellation and status markup safety."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_approval import Tty, project_in_git
from test_emit_verilog import EmitRunner, make, outcome, worker_result

from siliconsmith.approval import approve_project
from siliconsmith.cli import main
from siliconsmith.ledger import (
    LEDGER_REL,
    MAX_LEDGER_BYTES,
    MAX_RUNS,
    MAX_TESTS_PER_RUN,
    Approval,
    Digests,
    RunRecord,
    RunTest,
    append_run,
    load_ledger,
    set_approval,
)
from siliconsmith.project import Project
from siliconsmith.scaffold import create_project
from siliconsmith.status import _clip, compute_status
from siliconsmith.tools.emit_verilog import emit


def big_run() -> RunRecord:
    tests = [
        RunTest(name=f"test_{i:03d}_" + "x" * 190, module="m" * 100, covers=["R1"], outcome="pass")
        for i in range(MAX_TESTS_PER_RUN)
    ]
    return RunRecord(
        tool="run_tests",
        sim="icarus",
        at="2026-11-12T14:03:05Z",
        ok=True,
        stage="sim",
        image="example/toolchain:ctx-aaaa",
        siliconsmith_version="0",
        digests=Digests(check="a" * 64, sim="b" * 64),
        tests=tests,
    )


def test_a_full_ledger_still_loads_and_keeps_the_approval(tmp_path: Path) -> None:
    project, _ = create_project(tmp_path, "chip", "verilog", "Ada", "A chip.")
    set_approval(project.dir, Approval(spec_sha256="s" * 64, approved_by="Ada", at="t"))
    for _ in range(MAX_RUNS + 20):
        append_run(project.dir, big_run())
    path = project.dir / LEDGER_REL
    assert path.stat().st_size < MAX_LEDGER_BYTES
    loaded = load_ledger(project.dir)
    assert loaded.problem is None
    assert loaded.ledger.approval is not None and loaded.ledger.runs


def test_an_oversized_ledger_keeps_its_approval_when_replaced(tmp_path: Path) -> None:
    project, _ = create_project(tmp_path, "chip", "verilog", "Ada", "A chip.")
    set_approval(project.dir, Approval(spec_sha256="s" * 64, approved_by="Ada", at="t"))
    path = project.dir / LEDGER_REL
    path.write_bytes(path.read_bytes().rstrip()[:-1] + b',"pad":"' + b"x" * (9 << 20) + b'"}')
    assert load_ledger(project.dir).problem is not None
    append_run(project.dir, big_run())
    loaded = load_ledger(project.dir)
    assert loaded.problem is None and len(loaded.ledger.runs) == 1
    assert loaded.ledger.approval is not None
    assert (project.dir / ".siliconsmith" / "ledger.corrupt.json").exists()


def test_a_file_edited_during_a_run_is_stale_not_tested(tmp_path: Path) -> None:
    class Editing(EmitRunner):
        def run(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            (project_dir / "hdl" / "chip.py").write_text("# edited during the run\n")
            return super().run(*args, **kwargs)  # type: ignore[misc]

    runner = Editing(outcome(worker_result()))
    ctx, project_dir = make(tmp_path, "amaranth", runner)
    assert emit(ctx, "chip").ok
    report = compute_status(Project(project_dir, "chip", "amaranth"), ctx.image)
    check = next(s for s in report.steps if s.key == "check")
    assert check.state == "stale"


def test_approve_cancel_and_vanishing_spec_are_plain_refusals(git_repo: Path) -> None:
    project = project_in_git(git_repo)

    def eof(_: str) -> str:
        raise EOFError

    out = Tty()
    assert approve_project(project, stdin=Tty(), stdout=out, ask=eof) == 1
    assert "Not approved" in out.getvalue()

    def break_spec(_: str) -> str:
        (project.dir / "spec.yaml").write_text("title: x\n")
        return "approve"

    out = Tty()
    assert approve_project(project, stdin=Tty(), stdout=out, ask=break_spec) == 1
    assert "Refusing to approve" in out.getvalue()
    assert not (project.dir / LEDGER_REL).exists()


def test_status_text_cannot_inject_markup() -> None:
    clipped = _clip("failed: test_a<!-- [x](y) `z` \x00 b")
    assert (
        "<" not in clipped and "[" not in clipped and "`" not in clipped and "\x00" not in clipped
    )


def test_status_still_prints_when_the_report_file_is_blocked(
    git_repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = project_in_git(git_repo)
    (project.dir / "docs" / "verification.md").mkdir()
    assert main(["status", str(project.dir)]) == 0
    out = capsys.readouterr().out
    assert "Spec valid" in out and "was not updated" in out


def test_status_notes_a_different_image(git_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project = project_in_git(git_repo)
    run = big_run()
    run.tests = []
    append_run(project.dir, run)
    assert main(["status", str(project.dir), "--image", "other:tag"]) == 0
    assert "last run used image example/toolchain:ctx-aaaa" in capsys.readouterr().out
