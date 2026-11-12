# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Verify a recorded headless Claude Code demo, independently of what the agent claimed.

Usage: `uv run python docs/demos/verify_demo.py <demo folder> [--record]`

A demo folder `D` holds what `run_demo.py` captured: `meta.json`, `stream.jsonl` (the raw event
stream), `debug.log`, and `work/` (the folder the agent built its project in). This script:

1. checks the session was isolated (only Siliconsmith tools, no hooks, no project memory);
2. pulls out the numbers (turns, cost, tool calls, loop iterations);
3. takes a `status` snapshot of the agent's project through the real tool;
4. re-runs the design on copies, never touching the agent's folder: the agent's own tests, and
   then the frozen reference test, each on Icarus and on Verilator;
5. writes a sanitized Markdown transcript, and fails if it still leaks a path, e-mail or id;
6. with `--record`, files the transcript under `docs/demos/` and adds a row to the metrics table.

The raw stream is never written into the repository.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp import Client

from siliconsmith.runners.docker import DEFAULT_IMAGE
from siliconsmith.server import build_server

DEMOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = DEMOS_DIR.parent.parent
REFERENCE_TEST = REPO_ROOT / "tests" / "reference" / "test_counter_reference.py"
TOOL_PREFIX = "mcp__siliconsmith__"
ALLOWED_EXTRA_TOOLS = frozenset({"EndConversation"})
SIMULATORS = ("icarus", "verilator")
MARKER_START = "<!-- metrics:start -->"
MARKER_END = "<!-- metrics:end -->"
MAX_APPENDIX_LINES = 400
MAX_RESULT_CHARS = 2500

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
READ_MEMORY_RE = re.compile(
    r"(?i)\b(read|reading|load|loaded|loading)\b.*\b(CLAUDE\.md|AGENTS\.md)\b"
)
LANGUAGES = {
    ".py": "python",
    ".v": "verilog",
    ".sv": "systemverilog",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".json": "json",
}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str


# -- reading the captured files ----------------------------------------------------------------


def load_events(stream: Path) -> list[dict[str, Any]]:
    """The events in a stream-json file. Lines that are not JSON objects are skipped."""
    events: list[dict[str, Any]] = []
    if not stream.is_file():
        return events
    for line in stream.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def init_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def result_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    found = [e for e in events if e.get("type") == "result"]
    return found[-1] if found else None


def content_blocks(event: dict[str, Any]) -> list[dict[str, Any]]:
    content = (event.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def tool_uses(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every tool_use block the agent produced, in order."""
    return [
        b
        for e in events
        if e.get("type") == "assistant"
        for b in content_blocks(e)
        if b.get("type") == "tool_use"
    ]


def tool_results(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """tool_result blocks by tool_use id."""
    return {
        str(b.get("tool_use_id")): b
        for e in events
        if e.get("type") == "user"
        for b in content_blocks(e)
        if b.get("type") == "tool_result"
    }


def result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    parts = [c.get("text", "") for c in content or [] if isinstance(c, dict)]
    return "\n".join(p for p in parts if isinstance(p, str))


def result_summary(block: dict[str, Any]) -> str:
    """The summary text of a tool result (not its details)."""
    text = result_text(block).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    if isinstance(data, dict) and isinstance(data.get("summary"), str):
        return str(data["summary"])
    return text


def result_ok(block: dict[str, Any]) -> bool:
    if block.get("is_error"):
        return False
    try:
        data = json.loads(result_text(block))
    except json.JSONDecodeError:
        return bool(re.search(r'"ok"\s*:\s*true', result_text(block)))
    return isinstance(data, dict) and data.get("ok") is True


def short_name(tool: str) -> str:
    return tool.removeprefix(TOOL_PREFIX)


# -- a. isolation checks -----------------------------------------------------------------------


def isolation_checks(events: list[dict[str, Any]], debug_log: str | None) -> list[Check]:
    checks: list[Check] = []
    init = init_event(events)

    if init is None:
        checks.append(Check("init event", False, "the stream has no system init event"))
        init = {}
    else:
        checks.append(Check("init event", True, "found"))

    tools = [str(t) for t in init.get("tools") or []]
    extra = [t for t in tools if t not in ALLOWED_EXTRA_TOOLS and not t.startswith(TOOL_PREFIX)]
    names = {short_name(t) for t in tools}
    missing = [t for t in ("create_project", "run_tests") if t not in names]
    if not tools:
        checks.append(Check("only Siliconsmith tools", False, "init lists no tools"))
    elif extra:
        checks.append(Check("only Siliconsmith tools", False, f"other tools present: {extra}"))
    elif missing:
        checks.append(Check("only Siliconsmith tools", False, f"missing tools: {missing}"))
    else:
        checks.append(Check("only Siliconsmith tools", True, f"{len(tools)} tools, all ours"))

    servers = init.get("mcp_servers")
    good = [{"name": "siliconsmith", "status": "connected"}]
    shown = [{"name": s.get("name"), "status": s.get("status")} for s in servers or []]
    checks.append(
        Check(
            "one MCP server, connected",
            shown == good,
            "siliconsmith connected" if shown == good else f"mcp_servers is {shown}",
        )
    )

    plugins = init.get("plugins") or []
    outside = [p.get("name") for p in plugins if p.get("path") != "builtin"]
    checks.append(
        Check(
            "only built-in plugins",
            not outside,
            f"{len(plugins)} built-in plugins" if not outside else f"non-built-in: {outside}",
        )
    )

    hooks = [
        e for e in events if "hook" in str(e.get("type", "")) or "hook" in str(e.get("subtype", ""))
    ]
    checks.append(
        Check(
            "no hooks ran",
            not hooks,
            "no hook events"
            if not hooks
            else f"{len(hooks)} hook events, e.g. {hooks[0].get('subtype')}",
        )
    )

    mode = init.get("permissionMode")
    checks.append(
        Check("permission mode dontAsk", mode == "dontAsk", f"permissionMode is {mode!r}")
    )

    if debug_log is None:
        checks.append(Check("no project memory", False, "debug.log is missing"))
    else:
        reads = [
            ln.strip()
            for ln in debug_log.splitlines()
            if READ_MEMORY_RE.search(ln) and "project memory is off" not in ln
        ]
        if "project memory is off" not in debug_log:
            checks.append(
                Check("no project memory", False, "debug.log lacks 'project memory is off'")
            )
        elif reads:
            checks.append(Check("no project memory", False, f"memory file read: {reads[0][:120]}"))
        else:
            checks.append(Check("no project memory", True, "project memory is off"))

    foreign = sorted(
        {
            str(b.get("name"))
            for b in tool_uses(events)
            if not str(b.get("name")).startswith(TOOL_PREFIX)
            and b.get("name") not in ALLOWED_EXTRA_TOOLS
        }
    )
    checks.append(
        Check(
            "agent used only our tools",
            not foreign,
            "every tool call was a Siliconsmith tool"
            if not foreign
            else f"other tools called: {foreign}",
        )
    )

    final = result_event(events)
    checks.append(
        Check(
            "session finished",
            final is not None,
            "result event found" if final else "no result event: the session did not finish",
        )
    )
    return checks


# -- b. metrics --------------------------------------------------------------------------------


def extract_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    final = result_event(events) or {}
    uses = tool_uses(events)
    results = tool_results(events)
    by_tool = Counter(short_name(str(b.get("name"))) for b in uses)
    runs = [b for b in uses if short_name(str(b.get("name"))) == "run_tests"]
    ok_runs = sum(
        1 for b in runs if str(b.get("id")) in results and result_ok(results[str(b["id"])])
    )
    return {
        "num_turns": final.get("num_turns"),
        "cost_usd": final.get("total_cost_usd"),
        "duration_min": round(final["duration_ms"] / 60000, 1)
        if final.get("duration_ms")
        else None,
        "is_error": final.get("is_error"),
        "result_subtype": final.get("subtype"),
        "tool_calls": dict(sorted(by_tool.items())),
        "run_tests_calls": len(runs),
        "run_tests_ok": ok_runs,
        "approve_calls": sum(n for tool, n in by_tool.items() if "approve" in tool.lower()),
    }


# -- c and d. the project, the status snapshot, the independent re-runs ------------------------


class DemoError(Exception):
    """The demo folder cannot be verified (the reason is the message)."""


def find_project(work: Path) -> Path:
    found = sorted(p.parent for p in work.glob("*/siliconsmith.toml"))
    if len(found) != 1:
        raise DemoError(
            f"expected exactly one project (a folder with siliconsmith.toml) under {work.name}/, "
            f"found {len(found)}"
        )
    return found[0]


async def call_tool(client: Client, tool: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    if result.is_error or result.structured_content is None:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        raise DemoError(f"{tool} failed: {text[:300]}")
    out: dict[str, Any] = result.structured_content
    return out


@dataclass
class StatusSnapshot:
    table: str
    summary: str


def summarize_status(steps: list[dict[str, Any]]) -> str:
    """`spec valid, check, sim x2, coverage pass; approval open` from the status steps."""
    state = {s["key"]: s["state"] for s in steps}
    done: list[str] = []
    if state.get("spec_valid") == "pass":
        done.append("spec valid")
    if state.get("check") == "pass":
        done.append("check")
    sims = sum(1 for k in ("sim_icarus", "sim_verilator") if state.get(k) == "pass")
    if sims:
        done.append(f"sim x{sims}")
    if state.get("coverage") == "pass":
        done.append("coverage pass")
    missing = [
        f"{k} {state[k]}"
        for k in ("spec_valid", "check", "sim_icarus", "sim_verilator", "coverage")
        if state.get(k, "not_run") != "pass"
    ]
    text = ", ".join(done) or "nothing passes"
    if missing:
        text += " (" + ", ".join(missing) + ")"
    approval = "approved" if state.get("spec_approved") == "pass" else "open"
    return f"{text}; approval {approval}"


async def status_snapshot(root: Path, name: str, image: str) -> StatusSnapshot:
    server = build_server(root, None, image=image)
    async with Client(server) as client:
        out = await call_tool(client, "status", project=name)
    return StatusSnapshot(out["summary"], summarize_status(out["details"]["steps"]))


@dataclass
class Rerun:
    label: str  # agent or reference
    simulator: str
    ok: bool
    passed: int
    failed: int
    detail: str  # the tool's first summary line, or why it could not run

    @property
    def success(self) -> bool:
        return self.ok and self.passed > 0 and self.failed == 0

    def text(self) -> str:
        return f"{self.passed} passed, {self.failed} failed"


def copy_project(source: Path, scratch_root: Path) -> Path:
    """Copy the project (without build/) to a fresh root; the folder name must stay the same."""
    if scratch_root.exists():
        shutil.rmtree(scratch_root)
    dest = scratch_root / source.name
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("build"))
    return dest


def use_reference_test(project: Path, reference: Path = REFERENCE_TEST) -> None:
    """Replace every test with the frozen reference test, as `test/test.py`."""
    test_dir = project / "test"
    test_dir.mkdir(exist_ok=True)
    for old in test_dir.glob("test*.py"):
        old.unlink()
    (test_dir / "test.py").write_text(reference.read_text())


async def run_sims(label: str, root: Path, name: str, image: str) -> list[Rerun]:
    runs: list[Rerun] = []
    server = build_server(root, None, image=image)
    async with Client(server) as client:
        for sim in SIMULATORS:
            try:
                out = await call_tool(client, "run_tests", project=name, simulator=sim)
            except DemoError as exc:
                runs.append(Rerun(label, sim, False, 0, 0, str(exc)))
                continue
            tests = out.get("details", {}).get("tests", [])
            passed = sum(1 for t in tests if t.get("outcome") == "pass")
            failed = sum(1 for t in tests if t.get("outcome") in ("fail", "error"))
            first = out["summary"].splitlines()[0] if out["summary"] else ""
            runs.append(Rerun(label, sim, bool(out.get("ok")), passed, failed, first))
    return runs


async def rerun_all(project: Path, scratch: Path, image: str) -> list[Rerun]:
    """The four independent runs, on copies under `scratch`."""
    agent = copy_project(project, scratch / "agent")
    runs = await run_sims("agent", agent.parent, agent.name, image)
    reference = copy_project(project, scratch / "reference")
    use_reference_test(reference)
    runs += await run_sims("reference", reference.parent, reference.name, image)
    return runs


# -- e. sanitized transcript and leak check ----------------------------------------------------


def sanitize(text: str, demo_dir: Path) -> str:
    """Replace the demo folder, the home directory, e-mail addresses and ids."""
    for path in sorted({str(demo_dir), str(demo_dir.resolve())}, key=len, reverse=True):
        text = text.replace(path, "<demo>")
    for home in sorted({str(Path.home()), str(Path.home().resolve())}, key=len, reverse=True):
        text = text.replace(home, "~")
    text = EMAIL_RE.sub("<email>", text)
    return UUID_RE.sub("<id>", text)


def leak_check(text: str) -> list[str]:
    """What a published transcript must not contain. An empty list means clean."""
    problems = []
    for needle in ("/home/", "/tmp/ss-demo", "session_id", "system prompt"):
        if needle in text:
            problems.append(f"contains {needle!r}")
    if EMAIL_RE.search(text):
        problems.append("contains an e-mail address (something@domain)")
    for field_name in (
        '"mcp_servers"',
        '"slash_commands"',
        '"permissionMode"',
        '"claude_code_version"',
    ):
        if field_name in text:
            problems.append(f"contains init field {field_name}")
    if UUID_RE.search(text):
        problems.append("contains a uuid")
    return problems


def fence(body: str, lang: str = "") -> str:
    ticks = "```"
    while ticks in body:
        ticks += "`"
    return f"{ticks}{lang}\n{body.rstrip()}\n{ticks}"


def language_for(path: str) -> str:
    return LANGUAGES.get(Path(path).suffix, "")


def render_arguments(args: dict[str, Any]) -> str:
    """Short arguments inline, file contents in fenced code blocks."""
    inline: list[str] = []
    blocks: list[str] = []
    path = str(args.get("path", ""))
    for key, value in args.items():
        if isinstance(value, str) and ("\n" in value or len(value) > 80):
            lang = language_for(path) if key == "content" else ""
            if key == "content" and not lang and path == "":
                lang = "yaml"
            blocks.append(f"`{key}`:\n\n{fence(value, lang)}")
        else:
            inline.append(f"{key}={json.dumps(value)}")
    head = "  " + ", ".join(inline) if inline else ""
    return head + ("\n\n" + "\n\n".join(blocks) if blocks else "")


def clip(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def transcript_body(events: list[dict[str, Any]]) -> list[str]:
    results = tool_results(events)
    out: list[str] = []
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in content_blocks(event):
            kind = block.get("type")
            if kind == "text" and str(block.get("text", "")).strip():
                out.append(f"**Agent:** {str(block['text']).strip()}")
            elif kind == "tool_use":
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                out.append(
                    f"**Call** `{short_name(str(block.get('name')))}`{render_arguments(args)}"
                )
                got = results.get(str(block.get("id")))
                if got is not None:
                    label = "Error" if got.get("is_error") else "Result"
                    out.append(f"**{label}:**\n\n{fence(clip(result_summary(got)))}")
    final = result_event(events)
    if final is not None:
        out.append(f"**Final message:** {str(final.get('result', '')).strip()}")
    return out


def appendix(project: Path) -> list[str]:
    """The final design, spec and test files from the agent's project."""
    wanted = [project / "spec.yaml"]
    wanted += sorted((project / "hdl").glob("*.py"))
    wanted += [p for p in sorted((project / "src").glob("*.v")) if not p.name.startswith("tt_um_")]
    wanted += sorted((project / "test").rglob("*.py"))
    out = ["## Appendix: the final files", ""]
    for path in wanted:
        if not path.is_file():
            continue
        lines = path.read_text(errors="replace").splitlines()
        if len(lines) > MAX_APPENDIX_LINES:
            lines = [
                *lines[:MAX_APPENDIX_LINES],
                f"... ({len(lines) - MAX_APPENDIX_LINES} more lines)",
            ]
        rel = path.relative_to(project).as_posix()
        out += [f"### `{rel}`", "", fence("\n".join(lines), language_for(rel)), ""]
    return out


def build_transcript(
    meta: dict[str, Any],
    events: list[dict[str, Any]],
    project: Path | None,
    snapshot: StatusSnapshot | None,
    reruns: list[Rerun],
    checks: list[Check],
) -> str:
    lines = [
        f"# Demo transcript: {meta.get('frontend')} front-end",
        "",
        f"- Front-end: {meta.get('frontend')}",
        f"- Date (UTC): {meta.get('date')}",
        f"- Model: {meta.get('model')}",
        f"- Claude Code version: {meta.get('claude_code_version')}",
        f"- Siliconsmith commit: {meta.get('tools_sha')}"
        + (" (with uncommitted changes)" if meta.get("tools_dirty") else ""),
        f"- Prompt: `{meta.get('prompt')}`",
        "",
        "## Session",
        "",
    ]
    lines += [part + "\n" for part in transcript_body(events)]
    if project is not None:
        lines += appendix(project)
    lines += ["## Status table (taken before the re-runs)", ""]
    lines += [fence(snapshot.table) if snapshot else "(not available)", ""]
    lines += ["## Verification", ""]
    lines += ["| Check | Result | Detail |", "| --- | --- | --- |"]
    for check in checks:
        lines.append(f"| {check.name} | {'pass' if check.passed else 'FAIL'} | {check.reason} |")
    for run in reruns:
        lines.append(
            f"| {run.label} tests on {run.simulator} | {'pass' if run.success else 'FAIL'} "
            f"| {run.text()} |"
        )
    return "\n".join(lines) + "\n"


# -- f. recording ------------------------------------------------------------------------------


def next_transcript_name(demos_dir: Path, frontend: str, day: str) -> str:
    n = 1
    while (demos_dir / f"{frontend}-{day}-{n}.md").exists():
        n += 1
    return f"{frontend}-{day}-{n}.md"


def load_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    return data if isinstance(data, list) else []


def mark(ok: bool | None) -> str:
    return "n/a" if ok is None else ("pass" if ok else "FAIL")


def metrics_table(rows: list[dict[str, Any]]) -> str:
    head = (
        "| Date | Front-end | Model | Turns | run_tests calls | Cost (USD) | Minutes "
        "| Agent: Icarus | Agent: Verilator | Reference: Icarus | Reference: Verilator "
        "| Status | Passed | Transcript |"
    )
    lines = [head, "|" + " --- |" * 14]
    for r in rows:
        cost = r.get("cost_usd")
        lines.append(
            "| {date} | {fe} | {model} | {turns} | {runs} | {cost} | {mins} | {ai} | {av} "
            "| {ri} | {rv} | {status} | {passed} | [{t}]({t}) |".format(
                date=r.get("date", ""),
                fe=r.get("frontend", ""),
                model=r.get("model", ""),
                turns=r.get("num_turns", ""),
                runs=r.get("run_tests_calls", ""),
                cost="" if cost is None else f"{cost:.2f}",
                mins=r.get("duration_min", ""),
                ai=mark(r.get("agent_icarus")),
                av=mark(r.get("agent_verilator")),
                ri=mark(r.get("reference_icarus")),
                rv=mark(r.get("reference_verilator")),
                status=r.get("status_summary", ""),
                passed="yes" if r.get("passed") else "no",
                t=r.get("transcript", ""),
            )
        )
    return "\n".join(lines)


def update_readme(readme: Path, rows: list[dict[str, Any]]) -> None:
    """Replace the table between the metrics markers. Running it twice changes nothing."""
    text = readme.read_text()
    start, end = text.find(MARKER_START), text.find(MARKER_END)
    if start < 0 or end < start:
        raise DemoError(f"{readme} has no {MARKER_START} ... {MARKER_END} markers")
    table = metrics_table(rows) if rows else "_No attempts recorded yet._"
    readme.write_text(text[: start + len(MARKER_START)] + "\n" + table + "\n" + text[end:])


def record_attempt(
    demos_dir: Path,
    transcript: str,
    meta: dict[str, Any],
    metrics: dict[str, Any],
    reruns: list[Rerun],
    status_summary: str,
    passed: bool,
) -> str:
    """Copy the transcript, append a row to metrics.json, regenerate the README table."""
    day = str(meta.get("date", ""))[:10].replace("-", "") or datetime.now(UTC).strftime("%Y%m%d")
    name = next_transcript_name(demos_dir, str(meta["frontend"]), day)
    (demos_dir / name).write_text(transcript)
    by = {(r.label, r.simulator): r.success for r in reruns}
    row = {
        "date": str(meta.get("date", ""))[:10],
        "frontend": meta.get("frontend"),
        "model": meta.get("model"),
        "claude_code_version": meta.get("claude_code_version"),
        "tools_sha": meta.get("tools_sha"),
        "tools_dirty": meta.get("tools_dirty"),
        "num_turns": metrics.get("num_turns"),
        "run_tests_calls": metrics.get("run_tests_calls"),
        "cost_usd": metrics.get("cost_usd"),
        "duration_min": metrics.get("duration_min"),
        "agent_icarus": by.get(("agent", "icarus")),
        "agent_verilator": by.get(("agent", "verilator")),
        "reference_icarus": by.get(("reference", "icarus")),
        "reference_verilator": by.get(("reference", "verilator")),
        "status_summary": status_summary,
        "passed": passed,
        "transcript": name,
    }
    path = demos_dir / "metrics.json"
    rows = [*load_metrics(path), row]
    path.write_text(json.dumps(rows, indent=2) + "\n")
    update_readme(demos_dir / "README.md", rows)
    return name


# -- putting it together -----------------------------------------------------------------------


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    reruns: list[Rerun] = field(default_factory=list)
    status_summary: str = ""
    transcript: str = ""
    leaks: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            all(c.passed for c in self.checks)
            and len(self.reruns) == 4
            and all(r.success for r in self.reruns)
            and not self.leaks
        )


async def verify_async(demo: Path, image: str = DEFAULT_IMAGE) -> Report:
    meta_path = demo / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    events = load_events(demo / "stream.jsonl")
    debug = demo / "debug.log"
    report = Report(meta=meta)
    report.checks = isolation_checks(
        events, debug.read_text(errors="replace") if debug.is_file() else None
    )
    report.metrics = extract_metrics(events)
    report.checks.append(
        Check(
            "agent never approved",
            report.metrics["approve_calls"] == 0,
            f"{report.metrics['approve_calls']} approve calls",
        )
    )

    project: Path | None = None
    snapshot: StatusSnapshot | None = None
    try:
        project = find_project(demo / "work")
        report.checks.append(Check("one project built", True, project.name))
        # The snapshot comes first: it shows what the agent left, before any re-run.
        snapshot = await status_snapshot(project.parent, project.name, image)
        report.status_summary = snapshot.summary
        scratch = demo / "verify"
        report.reruns = await rerun_all(project, scratch, image)
    except DemoError as exc:
        report.checks.append(Check("one project built", False, str(exc)))

    report.transcript = sanitize(
        build_transcript(meta, events, project, snapshot, report.reruns, report.checks), demo
    )
    report.leaks = leak_check(report.transcript)
    if report.leaks:
        report.checks.append(Check("transcript has no leaks", False, "; ".join(report.leaks)))
    else:
        report.checks.append(Check("transcript has no leaks", True, "clean"))
    (demo / "transcript.md").write_text(report.transcript)
    return report


def print_report(report: Report) -> None:
    print("Isolation and bookkeeping checks:")
    for check in report.checks:
        print(f"  [{'pass' if check.passed else 'FAIL'}] {check.name}: {check.reason}")
    print("Independent re-runs (on copies):")
    for run in report.reruns:
        print(
            f"  [{'pass' if run.success else 'FAIL'}] {run.label} on {run.simulator}: {run.text()}"
        )
    m = report.metrics
    print(
        f"Metrics: {m['num_turns']} turns, {m['run_tests_calls']} run_tests calls "
        f"({m['run_tests_ok']} ok), cost {m['cost_usd']}, {m['duration_min']} min, "
        f"result {m['result_subtype']}"
    )
    print(f"Status: {report.status_summary}")
    print("VERIFIED" if report.passed else "NOT VERIFIED")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a recorded Siliconsmith demo folder")
    parser.add_argument("demo", type=Path, help="the demo folder made by run_demo.py")
    parser.add_argument("--record", action="store_true", help="file the transcript and metrics row")
    args = parser.parse_args(argv)
    demo = args.demo.resolve()
    report = asyncio.run(verify_async(demo))
    print_report(report)
    if args.record:
        name = record_attempt(
            DEMOS_DIR,
            report.transcript,
            report.meta,
            report.metrics,
            report.reruns,
            report.status_summary,
            report.passed,
        )
        print(f"Recorded docs/demos/{name} and updated metrics.json and README.md")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
