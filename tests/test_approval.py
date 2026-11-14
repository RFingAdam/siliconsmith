# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`siliconsmith approve`: terminal only, owner only, and no MCP tool can approve."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest
from mcp import Client
from test_status import SPEC

from siliconsmith import tools
from siliconsmith.approval import REFUSAL, approve_project
from siliconsmith.cli import main
from siliconsmith.ledger import LEDGER_REL, load_ledger
from siliconsmith.project import Project
from siliconsmith.scaffold import create_project
from siliconsmith.server import build_server
from siliconsmith.status import compute_status

IMAGE = "example/toolchain:ctx-aaaa"


class Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def project_in_git(git_repo: Path) -> Project:
    project, _ = create_project(git_repo, "chip", "verilog", "Ada Lovelace", "A chip.")
    (project.dir / "spec.yaml").write_text(SPEC)
    return project


def test_refuses_without_a_terminal(tmp_path: Path) -> None:
    project, _ = create_project(tmp_path, "chip", "verilog", "Ada", "A chip.")
    for stdin, stdout in ((io.StringIO("approve\n"), Tty()), (Tty("approve\n"), io.StringIO())):
        out = stdout
        code = approve_project(
            project, stdin=stdin, stdout=out, ask=lambda _: pytest.fail("must not ask")
        )
        assert code == 1
        assert REFUSAL in out.getvalue()
    assert not (project.dir / LEDGER_REL).exists()


def test_cli_refuses_without_a_terminal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    project, _ = create_project(tmp_path, "chip", "verilog", "Ada", "A chip.")
    # Under pytest stdin and stdout are not terminals.
    assert main(["approve", str(project.dir)]) == 1
    assert "must be run by a person at a terminal" in capsys.readouterr().out
    assert not (project.dir / LEDGER_REL).exists()


def test_approval_records_name_hash_and_time(git_repo: Path) -> None:
    project = project_in_git(git_repo)
    out = Tty()
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return " approve "

    assert approve_project(project, stdin=Tty(), stdout=out, ask=ask) == 0
    text = out.getvalue()
    assert "R1: First thing." in text and "R2: Second thing." in text
    sha = hashlib.sha256((project.dir / "spec.yaml").read_bytes()).hexdigest()
    assert sha in text and "Chip" in text
    assert "honest-use convention, not a security boundary" in text
    assert len(asked) == 1
    approval = load_ledger(project.dir).ledger.approval
    assert approval is not None
    assert (approval.approved_by, approval.spec_sha256) == ("Test", sha)
    assert approval.at.endswith("Z")
    step = compute_status(project, IMAGE).steps[1]
    assert step.state == "pass" and "Test" in step.evidence


def test_anything_else_cancels(git_repo: Path) -> None:
    project = project_in_git(git_repo)
    out = Tty()
    assert approve_project(project, stdin=Tty(), stdout=out, ask=lambda _: "yes") == 1
    assert "Nothing was recorded" in out.getvalue()
    assert load_ledger(project.dir).ledger.approval is None


def test_an_invalid_spec_cannot_be_approved(git_repo: Path) -> None:
    project = project_in_git(git_repo)
    (project.dir / "spec.yaml").write_text("title: x\n")
    out = Tty()
    code = approve_project(
        project, stdin=Tty(), stdout=out, ask=lambda _: pytest.fail("must not ask")
    )
    assert code == 1 and "not valid" in out.getvalue()
    assert load_ledger(project.dir).ledger.approval is None


def test_spec_changing_while_reading_is_not_approved(git_repo: Path) -> None:
    project = project_in_git(git_repo)

    def ask(_: str) -> str:
        (project.dir / "spec.yaml").write_text(SPEC + "open_questions:\n  - Hm?\n")
        return "approve"

    out = Tty()
    assert approve_project(project, stdin=Tty(), stdout=out, ask=ask) == 1
    assert "changed while you were reading" in out.getvalue()
    assert load_ledger(project.dir).ledger.approval is None


def test_falls_back_to_the_os_user_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from siliconsmith import approval

    monkeypatch.setattr(
        approval.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    monkeypatch.setattr(approval.getpass, "getuser", lambda: "osname")
    assert approval.git_user_name(tmp_path) == "osname"


# -- no MCP tool approves, and the generated doc is not writable ---------------------------


@pytest.mark.anyio
async def test_no_tool_can_approve(tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, None)) as client:
        listed = (await client.list_tools()).tools
    for tool in listed:
        text = f"{tool.name} {tool.description or ''}".lower()
        assert "approve" not in tool.name
        schema = str(tool.input_schema).lower()
        assert "approv" not in schema
        # Descriptions may only say that no tool approves.
        if "approv" in text:
            assert tool.name == "status" and "no tool can do it" in text
    assert not any("approve" in name for name in tools.TOOL_MODULES)


@pytest.mark.anyio
async def test_generated_docs_and_ledger_are_not_writable(tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, None)) as client:
        made = await client.call_tool(
            "create_project",
            {"name": "chip", "frontend": "verilog", "author": "Ada", "description": "d"},
        )
        assert not made.is_error
        for tool in ("write_rtl", "write_test"):
            for path in (
                "docs/verification.md",
                ".siliconsmith/ledger.json",
                ".siliconsmith/ledger.corrupt.json",
            ):
                result = await client.call_tool(
                    tool, {"project": "chip", "path": path, "content": "x"}
                )
                assert result.is_error, (tool, path)
        assert not (tmp_path / "chip" / "docs" / "verification.md").exists()
        assert not (tmp_path / "chip" / LEDGER_REL).exists()
