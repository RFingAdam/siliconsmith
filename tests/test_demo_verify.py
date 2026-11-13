# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

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


GOOD_META = {"memory_isolation": {"ancestors_clear": True, "claude_mds_disabled": True}}


def by_name(checks: list[Any]) -> dict[str, Any]:
    return {c.name: c for c in checks}


def failed(
    events: list[dict[str, Any]], debug: str | None, meta: dict[str, Any] | None = GOOD_META
) -> set[str]:
    return {c.name for c in vd.isolation_checks(events, debug, meta) if not c.passed}


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


def test_plugins_must_include_the_memory_plugin(events: list[dict[str, Any]], debug: str) -> None:
    init_of(events)["plugins"] = []
    assert failed(events, debug) == {"only built-in plugins"}
    del init_of(events)["plugins"]
    assert failed(events, debug) == {"only built-in plugins"}


def test_a_real_debug_log_without_a_marker_line_passes(events: list[dict[str, Any]]) -> None:
    # Real runs never log "project memory is off"; the runner record is the evidence.
    assert failed(events, "[DEBUG] nothing here\n") == set()


def test_memory_check_needs_the_runner_record(events: list[dict[str, Any]], debug: str) -> None:
    assert failed(events, debug, None) == {"no project memory"}
    assert failed(events, debug, {}) == {"no project memory"}
    off = {"memory_isolation": {"ancestors_clear": True, "claude_mds_disabled": False}}
    assert failed(events, debug, off) == {"no project memory"}
    assert failed(events, None) == {"no project memory"}


@pytest.mark.parametrize("name", ["CLAUDE.md", "AGENTS.md", "CLAUDE.local.md", "claude.md"])
def test_any_mention_of_a_memory_file_fails(
    events: list[dict[str, Any]], debug: str, name: str
) -> None:
    assert failed(events, debug + f"[DEBUG] something about {name} here\n") == {"no project memory"}


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


@pytest.mark.parametrize(
    ("subtype", "is_error"),
    [("error_max_turns", True), ("error_during_execution", True), ("success", True)],
)
def test_a_session_that_did_not_succeed_fails(
    events: list[dict[str, Any]], debug: str, subtype: str, is_error: bool
) -> None:
    final = next(e for e in events if e["type"] == "result")
    final["subtype"], final["is_error"] = subtype, is_error
    assert failed(events, debug) == {"session finished"}


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
    # The demo agents do not run synthesis or the gates; the line says so instead of hiding it.
    assert vd.summarize_status(steps) == (
        "spec valid, check, sim x2, coverage pass (synthesis not_run, gate_sim not_run); "
        "approval open"
    )
    steps += [{"key": "synthesis", "state": "pass"}, {"key": "gate_sim", "state": "pass"}]
    assert vd.summarize_status(steps) == (
        "spec valid, check, sim x2, coverage pass, synthesis pass, gate-level pass; approval open"
    )
    steps[3]["state"] = "stale"
    assert "sim_verilator stale" in vd.summarize_status(steps)
    steps[-1]["state"] = "fail"
    assert "gate_sim fail" in vd.summarize_status(steps)


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
    (project / "test" / "helper.py").write_text("agent")
    (project / "test" / "sitecustomize.py").write_text("agent")
    (project / "test" / "tb.v").write_text("harness")
    (project / "test" / "sub").mkdir()
    (project / "test" / "sub" / "x.py").write_text("agent")
    (project / "build").mkdir()
    (project / "build" / "x").write_text("x")
    copy_root = tmp_path / "scratch"
    copied = vd.copy_project(project, copy_root)
    assert copied == copy_root / "p" and not (copied / "build").exists()
    vd.use_reference_test(copied)
    assert sorted(p.name for p in (copied / "test").iterdir()) == ["tb.v", "test.py"]
    assert "test_counter_reference" in (copied / "test" / "test.py").read_text()
    assert (project / "test" / "test.py").read_text() == "agent"  # the original is untouched


def test_startup_hooks_are_found(tmp_path: Path) -> None:
    (tmp_path / "test").mkdir()
    assert vd.find_startup_hooks(tmp_path) == []
    (tmp_path / "test" / "sitecustomize.py").write_text("")
    (tmp_path / "hdl").mkdir()
    (tmp_path / "hdl" / "x.pth").write_text("")
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "usercustomize.py").write_text("")  # tool output, not scanned
    assert vd.find_startup_hooks(tmp_path) == ["hdl/x.pth", "test/sitecustomize.py"]


def test_reference_rerun_must_run_exactly_the_reference_test() -> None:
    ok = vd.Rerun("reference", "icarus", True, 1, 0, "", ["test_counter_reference"])
    assert ok.success
    assert not vd.Rerun("reference", "icarus", True, 1, 0, "", ["test_other"]).success
    assert not vd.Rerun(
        "reference", "icarus", True, 2, 0, "", ["test_counter_reference", "t2"]
    ).success
    assert vd.Rerun("agent", "icarus", True, 4, 0, "", ["a", "b"]).success


# -- the project matches the session (replay) ----------------------------------------------


@pytest.fixture
def replay_project(tmp_path: Path) -> Path:
    """The project the fixture stream describes: created, then src/counter.v written."""
    from siliconsmith.scaffold import create_project

    project, _ = create_project(tmp_path, "counter", "verilog", "A", "d", None)
    (project.dir / "src" / "counter.v").write_text("module counter(input clk);\nendmodule\n")
    return project.dir


def replay(events: list[dict[str, Any]], project: Path) -> Any:
    (check,) = vd.replay_checks(events, project, {"frontend": "verilog"})
    return check


def test_replay_accepts_a_project_the_session_wrote(
    events: list[dict[str, Any]], replay_project: Path
) -> None:
    assert replay(events, replay_project).passed


def test_replay_rejects_a_hand_edited_file(
    events: list[dict[str, Any]], replay_project: Path
) -> None:
    (replay_project / "src" / "counter.v").write_text(
        "module counter(input clk); // edited\nendmodule\n"
    )
    check = replay(events, replay_project)
    assert not check.passed and "src/counter.v" in check.reason


@pytest.mark.parametrize("rel", ["test/helper.py", "spec.yaml", "test/test.py"])
def test_replay_rejects_files_the_session_never_wrote(
    events: list[dict[str, Any]], replay_project: Path, rel: str
) -> None:
    (replay_project / rel).write_text("# not from the session\n")
    check = replay(events, replay_project)
    assert not check.passed and rel in check.reason


def test_replay_needs_the_project_to_be_the_created_one(
    events: list[dict[str, Any]], replay_project: Path
) -> None:
    renamed = replay_project.parent / "other"
    replay_project.rename(renamed)
    assert not replay(events, renamed).passed


def test_replay_ignores_a_write_the_tool_refused(
    events: list[dict[str, Any]], replay_project: Path
) -> None:
    refused = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "bad",
                    "name": "mcp__siliconsmith__write_test",
                    "input": {"project": "counter", "path": "test/test.py", "content": "x"},
                }
            ]
        },
    }
    answer = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "bad", "is_error": True, "content": "no"}
            ]
        },
    }
    events[2:2] = [refused, answer]
    assert replay(events, replay_project).passed


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
        vd.Rerun(lab, sim, True, 1, 0, "", ["test_counter_reference"])
        for lab in ("agent", "reference")
        for sim in vd.SIMULATORS
    ]
    reruns.append(vd.Rerun("reference", "icarus-gl", True, 1, 0, "", ["test_counter_reference"]))
    first = vd.record_attempt(
        demos, "# t\n", meta, metrics, reruns, "spec valid; approval open", True
    )
    reruns[3] = vd.Rerun("reference", "verilator", False, 0, 1, "", ["test_counter_reference"])
    second = vd.record_attempt(demos, "# t\n", meta, metrics, reruns, "x", False)
    assert (first, second) == ("verilog-20261112-1.md", "verilog-20261112-2.md")
    rows = json.loads((demos / "metrics.json").read_text())
    assert [r["passed"] for r in rows] == [True, False]
    assert rows[1]["reference_verilator"] is False and rows[0]["agent_icarus"] is True
    assert rows[0]["reference_icarus_gl"] is True
    readme = (demos / "README.md").read_text()
    assert readme.count("verilog-20261112-") == 4  # label and link, two rows
    assert rows[0]["result_subtype"] is None and "(exit " in readme
    assert readme.startswith("intro") and readme.rstrip().endswith("outro")
    # Regenerating from the same rows changes nothing.
    vd.update_readme(demos / "README.md", rows)
    assert (demos / "README.md").read_text() == readme


def test_readme_without_markers_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("no markers")
    with pytest.raises(vd.DemoError):
        vd.update_readme(tmp_path / "README.md", [])


def test_a_failed_leak_check_withholds_the_transcript(tmp_path: Path) -> None:
    demos = make_demos_dir(tmp_path)
    meta = {"frontend": "verilog", "date": "2026-11-12T09:00:00Z", "model": "m"}
    leaks = vd.leak_check("see /home/x and session_id")
    name = vd.record_attempt(demos, "SECRET /home/x\n", meta, {}, [], "s", False, leaks)
    assert name.startswith("withheld: leak check failed")
    assert not list(demos.glob("verilog-*.md"))  # nothing copied
    (row,) = json.loads((demos / "metrics.json").read_text())
    assert row["passed"] is False and row["transcript"] == name
    assert "/home/" not in (demos / "README.md").read_text() + (demos / "metrics.json").read_text()
    assert "withheld" in (demos / "README.md").read_text()


def test_a_leaking_metrics_row_is_refused(tmp_path: Path) -> None:
    demos = make_demos_dir(tmp_path)
    meta = {"frontend": "verilog", "date": "2026-11-12T09:00:00Z", "model": "/home/x/model"}
    with pytest.raises(vd.DemoError, match="would leak"):
        vd.record_attempt(demos, "# t\n", meta, {}, [], "s", True)
    assert not (demos / "metrics.json").exists() and not list(demos.glob("verilog-*.md"))


def test_the_same_demo_is_not_recorded_twice(tmp_path: Path) -> None:
    demos = make_demos_dir(tmp_path)
    meta = {"frontend": "verilog", "date": "2026-11-12T09:00:00Z", "model": "m", "demo_id": "d1"}
    vd.record_attempt(demos, "# t\n", meta, {}, [], "s", True)
    with pytest.raises(vd.DemoError, match="already recorded"):
        vd.record_attempt(demos, "# t\n", meta, {}, [], "s", True)
    vd.record_attempt(demos, "# t\n", {**meta, "demo_id": "d2"}, {}, [], "s", True)
    assert len(json.loads((demos / "metrics.json").read_text())) == 2
