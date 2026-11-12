# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""docs/demos/verify_demo.py against the real toolchain, on a fake demo folder."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT
from mcp import Client
from test_demo_verify import vd  # the loaded docs/demos/verify_demo.py module

from integration.test_sim import counter_project
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "demo"


async def fake_demo(
    tmp_path: Path, runner: DockerRunner, image: str, *, design: str | None = None
) -> Path:
    """A demo folder whose work/ holds a counter made through the real tools."""
    demo = tmp_path / "ss-demo-fake"
    work = demo / "work"
    work.mkdir(parents=True)
    async with Client(build_server(work, "verilog", runner=runner, image=image)) as client:
        await counter_project(client, work, "verilog", design=design)
    shutil.copy(FIXTURE / "stream.jsonl", demo / "stream.jsonl")
    shutil.copy(FIXTURE / "debug.log", demo / "debug.log")
    (demo / "meta.json").write_text(
        '{"date": "2026-11-12T09:00:00Z", "frontend": "verilog", "model": "m",'
        ' "claude_code_version": "x", "tools_sha": "abc", "prompt": "p"}'
    )
    return demo


def runs(report: Any) -> dict[tuple[str, str], bool]:
    return {(r.label, r.simulator): r.success for r in report.reruns}


async def test_verify_passes_a_correct_counter(
    tmp_path: Path, runner: DockerRunner, image: str
) -> None:
    demo = await fake_demo(tmp_path, runner, image)
    report = await vd.verify_async(demo, image)
    assert runs(report) == {
        ("agent", "icarus"): True,
        ("agent", "verilator"): True,
        ("reference", "icarus"): True,
        ("reference", "verilator"): True,
    }
    assert [c for c in report.checks if not c.passed] == []
    assert report.passed
    # The fake agent never ran a test, so the snapshot (taken before the re-runs) shows that.
    assert report.status_summary.startswith("spec valid (check not_run")
    assert (demo / "transcript.md").is_file()
    assert vd.leak_check((demo / "transcript.md").read_text()) == []
    # The agent's own folder was never run in: the re-runs used copies.
    assert not (demo / "work" / "counter" / "build" / "sim").exists()
    assert (demo / "verify" / "agent" / "counter" / "build").exists()


async def test_verify_fails_the_seeded_wrap_bug(
    tmp_path: Path, runner: DockerRunner, image: str
) -> None:
    demo = await fake_demo(tmp_path, runner, image, design="verilog_wrap_bug.v")
    report = await vd.verify_async(demo, image)
    outcome = runs(report)
    assert outcome[("reference", "icarus")] is False
    assert outcome[("reference", "verilator")] is False
    assert not report.passed
