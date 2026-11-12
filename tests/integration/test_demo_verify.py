# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""docs/demos/verify_demo.py against the real toolchain, on a fake demo folder."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT
from mcp import Client
from test_demo_verify import vd  # the loaded docs/demos/verify_demo.py module

from integration.test_emit import ARGS
from integration.test_sim import counter_project
from siliconsmith.runners.docker import DockerRunner
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

FIXTURE = REPO_ROOT / "tests" / "fixtures" / "demo"


def tool_pair(n: int, tool: str, args: dict[str, Any], summary: str) -> list[dict[str, Any]]:
    """An assistant tool_use event and the ok tool_result that answers it."""
    name = f"mcp__siliconsmith__{tool}"
    body = json.dumps({"ok": True, "summary": summary, "details": {}})
    return [
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "id": f"w{n}", "name": name, "input": args}]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"w{n}",
                        "content": [{"type": "text", "text": body}],
                    }
                ]
            },
        },
    ]


def session_stream(project: Path) -> str:
    """The synthetic passing stream, with the writes that built `project` (read back from disk)."""
    events = [json.loads(line) for line in (FIXTURE / "stream.jsonl").read_text().splitlines()]
    drop = {"create_project", "write_rtl"}
    keep: list[dict[str, Any]] = []
    skip_ids: set[str] = set()
    for event in events:
        blocks = (event.get("message") or {}).get("content") or []
        for b in blocks:
            if b.get("type") == "tool_use" and b["name"].rsplit("__", 1)[-1] in drop:
                skip_ids.add(b["id"])
        if any(b.get("tool_use_id") in skip_ids or b.get("id") in skip_ids for b in blocks):
            continue
        keep.append(event)
    written = [
        *tool_pair(1, "create_project", {"name": "counter", "frontend": "verilog", **ARGS}, "ok"),
        *tool_pair(
            2,
            "write_rtl",
            {
                "project": "counter",
                "path": "src/counter.v",
                "content": (project / "src/counter.v").read_text(),
            },
            "ok",
        ),
        *tool_pair(
            3,
            "write_spec",
            {"project": "counter", "content": (project / "spec.yaml").read_text()},
            "ok",
        ),
        *tool_pair(
            4,
            "write_test",
            {
                "project": "counter",
                "path": "test/test.py",
                "content": (project / "test/test.py").read_text(),
            },
            "ok",
        ),
    ]
    keep[1:1] = written  # after the init event
    return "\n".join(json.dumps(e) for e in keep) + "\n"


async def fake_demo(
    tmp_path: Path, runner: DockerRunner, image: str, *, design: str | None = None
) -> Path:
    """A demo folder whose work/ holds a counter made through the real tools."""
    demo = tmp_path / "ss-demo-fake"
    work = demo / "work"
    work.mkdir(parents=True)
    async with Client(build_server(work, "verilog", runner=runner, image=image)) as client:
        project = await counter_project(client, work, "verilog", design=design)
    (demo / "stream.jsonl").write_text(session_stream(project))
    shutil.copy(FIXTURE / "debug.log", demo / "debug.log")
    meta = {
        "date": "2026-11-12T09:00:00Z",
        "frontend": "verilog",
        "model": "m",
        "claude_code_version": "x",
        "tools_sha": "abc",
        "prompt": "p",
        "memory_isolation": {"ancestors_clear": True, "claude_mds_disabled": True},
    }
    (demo / "meta.json").write_text(json.dumps(meta))
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
        ("reference", "icarus-gl"): True,
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
    assert outcome[("reference", "icarus-gl")] is False  # the gates have the same wrap bug
    assert not report.passed


SITECUSTOMIZE_ATTACK = """\
import atexit, os, re

def _hide_failures():
    path = os.environ.get("COCOTB_RESULTS_FILE")
    if path and os.path.exists(path):
        text = open(path).read()
        text = re.sub(r"<failure[^>]*/>|<failure.*?</failure>", "", text, flags=re.S)
        open(path, "w").write(text)

atexit.register(_hide_failures)
"""


async def test_a_sitecustomize_in_test_cannot_fake_the_reference_run(
    tmp_path: Path, runner: DockerRunner, image: str
) -> None:
    demo = await fake_demo(tmp_path, runner, image, design="verilog_wrap_bug.v")
    # The write tool now refuses this file, so put it on disk directly, as a tampered folder.
    (demo / "work" / "counter" / "test" / "sitecustomize.py").write_text(SITECUSTOMIZE_ATTACK)
    report = await vd.verify_async(demo, image)
    outcome = runs(report)
    # The attack works on the agent's own runs (it hides the failures), so the test is not vacuous.
    assert outcome[("agent", "icarus")] is True
    # The reference copy is rebuilt without the agent's files, so the seeded bug still fails it.
    assert outcome[("reference", "icarus")] is False
    assert outcome[("reference", "verilator")] is False
    failed = {c.name for c in report.checks if not c.passed}
    assert "no Python start-up hooks" in failed
    assert not report.passed


async def test_a_hand_edited_project_fails_the_replay_check(
    tmp_path: Path, runner: DockerRunner, image: str
) -> None:
    demo = await fake_demo(tmp_path, runner, image)
    source = demo / "work" / "counter" / "src" / "counter.v"
    source.write_text(source.read_text() + "// edited by hand after the session\n")
    report = await vd.verify_async(demo, image)
    assert {c.name for c in report.checks if not c.passed} == {"project matches the session"}
    assert not report.passed
