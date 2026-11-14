# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import importlib.util
import subprocess
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from siliconsmith.runners.base import WorkerOutcome

REPO_ROOT = Path(__file__).resolve().parent.parent

# Test data, and the cocotb reference test that only runs inside the container.
collect_ignore = ["fixtures", "golden", "reference"]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeRunner:
    """Runner that returns canned outcomes and records the calls it received."""

    def __init__(self, *outcomes: WorkerOutcome) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, Mapping[str, Any], Path, float]] = []

    def run(
        self,
        verb: str,
        request: Mapping[str, Any],
        *,
        project_dir: Path,
        timeout_s: float,
    ) -> WorkerOutcome:
        self.calls.append((verb, request, project_dir, timeout_s))
        if self.outcomes:
            return self.outcomes.pop(0)
        return WorkerOutcome(
            exit_code=0, result={"ok": True}, stdout_tail="", stderr_tail="", timed_out=False
        )


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def load_script() -> Any:
    """Import a file from scripts/ by name, e.g. load_script("check_trailers")."""

    def load(name: str) -> ModuleType:
        path = REPO_ROOT / "scripts" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"scripts_{name}", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """An empty git repository with a local identity."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (
        ["init", "-q"],
        ["config", "user.name", "Test"],
        ["config", "user.email", "test@example.com"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True)
    return repo
