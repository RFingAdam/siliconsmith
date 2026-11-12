# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""docs/demos/verify_demo.py: isolation checks, metrics, sanitizer and recording (no Docker)."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import REPO_ROOT

DEMOS = REPO_ROOT / "docs" / "demos"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "demo"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("verify_demo", DEMOS / "verify_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["verify_demo"] = module
    spec.loader.exec_module(module)
    return module


vd = _load()


@pytest.fixture
def events() -> list[dict[str, Any]]:
    return vd.load_events(FIXTURE / "stream.jsonl")  # type: ignore[no-any-return]


@pytest.fixture
def debug() -> str:
    return (FIXTURE / "debug.log").read_text()


def by_name(checks: list[Any]) -> dict[str, Any]:
    return {c.name: c for c in checks}


def failed(events: list[dict[str, Any]], debug: str | None) -> set[str]:
    return {c.name for c in vd.isolation_checks(events, debug) if not c.passed}


def init_of(events: list[dict[str, Any]]) -> dict[str, Any]:
    return next(e for e in events if e.get("subtype") == "init")  # type: ignore[no-any-return]


# -- isolation checks ----------------------------------------------------------------------


def test_passing_fixture_passes_every_check(events: list[dict[str, Any]], debug: str) -> None:
    assert failed(events, debug) == set()


def test_extra_tool_in_init_fails(events: list[dict[str, Any]], debug: str) -> None:
    init_of(events)["tools"].append("Bash")
    assert failed(events, debug) == {"only Siliconsmith tools"}


def test_end_conversation_is_allowed(events: list[dict[str, Any]], debug: str) -> None:
    init_of(events)["tools"].append("EndConversation")
    assert failed(events, debug) == set()


def test_missing_required_tool_fails(events: list[dict[str, Any]], debug: str) -> None:
    init = init_of(events)
    init["tools"] = [t for t in init["tools"] if not t.endswith("run_tests")]
    assert failed(events, debug) == {"only Siliconsmith tools"}


def test_non_builtin_plugin_fails(events: list[dict[str, Any]], debug: str) -> None:
    init_of(events)["plugins"].append({"name": "x", "path": "/home/u/plugin", "source": "x@y"})
    assert failed(events, debug) == {"only built-in plugins"}


def test_extra_or_unconnected_server_fails(events: list[dict[str, Any]], debug: str) -> None:
    init_of(events)["mcp_servers"][0]["status"] = "failed"
    assert failed(events, debug) == {"one MCP server, connected"}
    init_of(events)["mcp_servers"] = [
        {"name": "siliconsmith", "status": "connected"},
        {"name": "other", "status": "connected"},
    ]
    assert failed(events, debug) == {"one MCP server, connected"}


def test_hook_event_fails(events: list[dict[str, Any]], debug: str) -> None:
    events.insert(1, {"type": "system", "subtype": "hook_started", "hook_name": "x"})
    assert failed(events, debug) == {"no hooks ran"}


def test_permission_mode_must_be_dont_ask(events: list[dict[str, Any]], debug: str) -> None:
    init_of(events)["permissionMode"] = "bypassPermissions"
    assert failed(events, debug) == {"permission mode dontAsk"}


def test_missing_memory_marker_fails(events: list[dict[str, Any]]) -> None:
    assert failed(events, "[DEBUG] nothing here\n") == {"no project memory"}
    assert failed(events, None) == {"no project memory"}


def test_reading_a_memory_file_fails(events: list[dict[str, Any]], debug: str) -> None:
    assert failed(events, debug + "[DEBUG] Loaded CLAUDE.md from /x\n") == {"no project memory"}


def test_foreign_tool_use_fails(events: list[dict[str, Any]], debug: str) -> None:
    events.insert(
        2,
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "id": "z", "name": "Bash", "input": {}}]},
        },
    )
    assert failed(events, debug) == {"agent used only our tools"}


def test_missing_result_event_fails(events: list[dict[str, Any]], debug: str) -> None:
    assert failed([e for e in events if e["type"] != "result"], debug) == {"session finished"}


# -- metrics -------------------------------------------------------------------------------


def test_metrics_are_extracted(events: list[dict[str, Any]]) -> None:
    m = vd.extract_metrics(events)
    assert m["num_turns"] == 9
    assert m["cost_usd"] == 0.42
    assert m["duration_min"] == 5.0
    assert m["result_subtype"] == "success" and m["is_error"] is False
    assert m["tool_calls"] == {"create_project": 1, "run_tests": 2, "write_rtl": 1}
    assert (m["run_tests_calls"], m["run_tests_ok"]) == (2, 1)
    assert m["approve_calls"] == 0


def test_approve_calls_are_counted(events: list[dict[str, Any]]) -> None:
    bad = copy.deepcopy(events)
    bad.insert(
        2,
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "a",
                        "name": "mcp__siliconsmith__approve",
                        "input": {},
                    }
                ]
            },
        },
    )
    assert vd.extract_metrics(bad)["approve_calls"] == 1


def test_status_summary_wording() -> None:
    steps = [
        {"key": k, "state": "pass"}
        for k in ("spec_valid", "check", "sim_icarus", "sim_verilator", "coverage")
    ] + [{"key": "spec_approved", "state": "not_run"}]
    assert vd.summarize_status(steps) == "spec valid, check, sim x2, coverage pass; approval open"
    steps[3]["state"] = "stale"
    assert "sim_verilator stale" in vd.summarize_status(steps)


# -- project discovery ---------------------------------------------------------------------


def test_find_project_needs_exactly_one(tmp_path: Path) -> None:
    with pytest.raises(vd.DemoError):
        vd.find_project(tmp_path)
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "siliconsmith.toml").write_text("")
    assert vd.find_project(tmp_path) == tmp_path / "a"
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "siliconsmith.toml").write_text("")
    with pytest.raises(vd.DemoError):
        vd.find_project(tmp_path)


def test_reference_copy_replaces_every_test(tmp_path: Path) -> None:
    project = tmp_path / "p"
    (project / "test").mkdir(parents=True)
    (project / "test" / "test.py").write_text("agent")
    (project / "test" / "test_extra.py").write_text("agent")
    (project / "test" / "helper.py").write_text("keep")
    (project / "build").mkdir()
    (project / "build" / "x").write_text("x")
    copy_root = tmp_path / "scratch"
    copied = vd.copy_project(project, copy_root)
    assert copied == copy_root / "p" and not (copied / "build").exists()
    vd.use_reference_test(copied)
    assert sorted(p.name for p in (copied / "test").iterdir()) == ["helper.py", "test.py"]
    assert "test_counter_reference" in (copied / "test" / "test.py").read_text()
    assert (project / "test" / "test.py").read_text() == "agent"  # the original is untouched


# -- transcript, sanitizer, leak check -----------------------------------------------------


def test_sanitize_removes_paths_emails_and_ids(tmp_path: Path) -> None:
    demo = tmp_path / "ss-demo-x"
    text = (
        f"see {demo}/work/a.v and {Path.home()}/x by a.b@example.com "
        "id 11111111-2222-3333-4444-555555555555"
    )
    out = vd.sanitize(text, demo)
    assert "<demo>/work/a.v" in out and "~/x" in out and "<email>" in out and "<id>" in out
    assert vd.leak_check(out) == []


@pytest.mark.parametrize(
    "text",
    [
        "path /home/x/file",
        "folder /tmp/ss-demo-verilog.abc/work",
        "mail me at someone@example.com",
        "has session_id inside",
        'init {"mcp_servers": []}',
        "the system prompt says",
    ],
)
def test_leak_check_catches_leaks(text: str) -> None:
    assert vd.leak_check(text)


def test_leak_check_allows_decorators() -> None:
    assert vd.leak_check("@cocotb.test()\nasync def t(dut): ...") == []


def test_transcript_has_calls_results_and_code_blocks(events: list[dict[str, Any]]) -> None:
    meta = {"frontend": "verilog", "date": "2026-11-12T09:00:00Z", "model": "m", "prompt": "p"}
    checks = vd.isolation_checks(events, "project memory is off")
    text = vd.build_transcript(meta, events, None, None, [], checks)
    assert "**Call** `create_project`" in text
    assert "```verilog\nmodule counter(input clk);" in text  # file content, fenced with a language
    assert "Created project 'counter' (verilog)." in text  # the summary text
    assert '"details"' not in text and "next_hints" not in text
    assert "**Final message:** The counter passes on icarus." in text
    assert "session_id" not in vd.sanitize(text, Path("/tmp/none"))


# -- recording -----------------------------------------------------------------------------


def make_demos_dir(tmp_path: Path) -> Path:
    demos = tmp_path / "demos"
    demos.mkdir()
    (demos / "README.md").write_text(
        f"intro\n\n{vd.MARKER_START}\n_No attempts recorded yet._\n{vd.MARKER_END}\n\noutro\n"
    )
    return demos


def test_record_appends_rows_and_regenerates_the_table(tmp_path: Path) -> None:
    demos = make_demos_dir(tmp_path)
    meta = {"frontend": "verilog", "date": "2026-11-12T09:00:00Z", "model": "m"}
    metrics = {"num_turns": 9, "run_tests_calls": 2, "cost_usd": 0.42, "duration_min": 5.0}
    reruns = [
        vd.Rerun(lab, sim, True, 4, 0, "")
        for lab in ("agent", "reference")
        for sim in vd.SIMULATORS
    ]
    first = vd.record_attempt(
        demos, "# t\n", meta, metrics, reruns, "spec valid; approval open", True
    )
    reruns[3] = vd.Rerun("reference", "verilator", False, 0, 1, "")
    second = vd.record_attempt(demos, "# t\n", meta, metrics, reruns, "x", False)
    assert (first, second) == ("verilog-20261112-1.md", "verilog-20261112-2.md")
    rows = json.loads((demos / "metrics.json").read_text())
    assert [r["passed"] for r in rows] == [True, False]
    assert rows[1]["reference_verilator"] is False and rows[0]["agent_icarus"] is True
    readme = (demos / "README.md").read_text()
    assert readme.count("verilog-20261112-") == 4  # label and link, two rows
    assert readme.startswith("intro") and readme.rstrip().endswith("outro")
    # Regenerating from the same rows changes nothing.
    vd.update_readme(demos / "README.md", rows)
    assert (demos / "README.md").read_text() == readme


def test_readme_without_markers_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("no markers")
    with pytest.raises(vd.DemoError):
        vd.update_readme(tmp_path / "README.md", [])
