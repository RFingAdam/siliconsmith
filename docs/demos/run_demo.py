# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Record one headless Claude Code session that designs the demo counter, then verify it.

Usage: `uv run python docs/demos/run_demo.py --frontend {amaranth,myhdl,verilog}`

The session starts from an empty folder and one prompt. The agent can only call Siliconsmith's
MCP tools: no shell, no file editing, no web, no project memory files, no hooks. Everything is
captured in a demo folder under /tmp (outside any folder that has an AGENTS.md or CLAUDE.md),
then `verify_demo.py` checks it independently. Exit code 0 means the demo verified.

Add `--record` to file the transcript and a metrics row under docs/demos/ (the maintainer does
that for the real runs; the plain command only leaves files in /tmp).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import verify_demo

from siliconsmith.runners.docker import DEFAULT_IMAGE

PROMPT = "8-bit counter on uo_out, count enable on ui_in[0]"
DEFAULT_MODEL = "claude-sonnet-5-5"
FRONTENDS = ("amaranth", "myhdl", "verilog")
MEMORY_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md")
SETTINGS = json.dumps(
    {
        "pluginConfigs": {
            "cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}
        }
    }
)


def server_command() -> list[str]:
    """The command that starts this checkout's MCP server."""
    script = Path(sys.executable).parent / "siliconsmith-mcp"
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "siliconsmith.server"]


def check_no_memory_files(demo: Path) -> None:
    """Refuse a demo folder with an AGENTS.md or CLAUDE.md in it or in any parent folder."""
    for folder in (demo, *demo.parents):
        for name in MEMORY_FILES:
            if (folder / name).exists():
                raise SystemExit(
                    f"{folder / name} exists. Claude Code would read it and the agent would see "
                    "more than the prompt. Use a demo folder under /tmp."
                )


def check_image() -> None:
    proc = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", DEFAULT_IMAGE],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"The toolchain image {DEFAULT_IMAGE} is not on this machine.\n"
            f"Fetch it first: docker pull {DEFAULT_IMAGE}"
        )


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=verify_demo.REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()


def claude_version() -> str:
    proc = subprocess.run(["claude", "--version"], capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def write_inputs(demo: Path, frontend: str, model: str, max_turns: int) -> dict[str, Any]:
    """Create D/work, D/mcp.json and D/meta.json."""
    work = demo / "work"
    work.mkdir()
    config = {
        "mcpServers": {
            "siliconsmith": {
                "command": server_command()[0],
                "args": [
                    *server_command()[1:],
                    "--root",
                    str(work),
                    "--default-frontend",
                    frontend,
                ],
                "alwaysLoad": True,
                "timeout": 1800000,
            }
        }
    }
    (demo / "mcp.json").write_text(json.dumps(config, indent=2) + "\n")
    meta = {
        "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "frontend": frontend,
        "model": model,
        "max_turns": max_turns,
        "prompt": PROMPT,
        "claude_code_version": claude_version(),
        "tools_sha": git_output("rev-parse", "HEAD"),
        "tools_dirty": bool(git_output("status", "--porcelain")),
    }
    (demo / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def claude_argv(demo: Path, model: str, max_turns: int) -> list[str]:
    return [
        "claude",
        "-p",
        PROMPT,
        "--model",
        model,
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        str(demo / "mcp.json"),
        "--settings",
        SETTINGS,
        "--tools",
        "",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch",
        "--allowedTools",
        "mcp__siliconsmith__*",
        "--permission-mode",
        "dontAsk",
        "--include-hook-events",
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-turns",
        str(max_turns),
        "--no-session-persistence",
        "--debug-file",
        str(demo / "debug.log"),
    ]


def clean_env() -> dict[str, str]:
    """Only what the session needs: no API keys, no shell customisation, no memory files."""
    return {
        "HOME": os.environ["HOME"],
        "PATH": os.environ["PATH"],
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        # Without this, a tool call over 2 minutes is moved to the background and its result is
        # lost in `claude -p`.
        "CLAUDE_CODE_MCP_AUTO_BACKGROUND_MS": "0",
        "MCP_TOOL_TIMEOUT": "1800000",
    }


def update_meta(demo: Path, **fields: Any) -> None:
    path = demo / "meta.json"
    meta = json.loads(path.read_text())
    meta.update(fields)
    path.write_text(json.dumps(meta, indent=2) + "\n")


def stop_group(proc: subprocess.Popen[bytes], grace_s: float = 10) -> None:
    """Ask the process group to stop (so the MCP server can stop its containers), then kill it."""
    for sig, wait in ((signal.SIGTERM, grace_s), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(timeout=wait)
            break
        except subprocess.TimeoutExpired:
            continue
    proc.wait()


def kill_leftover_containers(work: Path) -> list[str]:
    """Kill simulator containers that still have this demo's folder mounted.

    Only containers started by the Siliconsmith runner (named ss-...) whose mounts come from
    under `work` are touched, so another session's containers are left alone.
    """
    listed = subprocess.run(
        ["docker", "ps", "-q", "--filter", "name=^/ss-"],
        capture_output=True,
        text=True,
        check=False,
    )
    killed: list[str] = []
    for cid in listed.stdout.split():
        info = subprocess.run(
            ["docker", "inspect", "--format", "{{range .Mounts}}{{.Source}}\n{{end}}", cid],
            capture_output=True,
            text=True,
            check=False,
        )
        if any(line.startswith(str(work)) for line in info.stdout.splitlines()):
            subprocess.run(["docker", "kill", cid], capture_output=True, check=False)
            killed.append(cid)
    return killed


def run_session(demo: Path, model: str, max_turns: int, timeout_min: float) -> int | None:
    """Run claude; return its exit code, or None if it had to be killed after the timeout."""
    with (
        (demo / "stream.jsonl").open("w") as out,
        (demo / "stderr.txt").open("w") as err,
        open(os.devnull) as nothing,
    ):
        proc = subprocess.Popen(
            claude_argv(demo, model, max_turns),
            cwd=demo / "work",
            env=clean_env(),
            stdin=nothing,  # otherwise claude waits 3 seconds for input
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout_min * 60)
        except subprocess.TimeoutExpired:
            stop_group(proc)
            return None
        except KeyboardInterrupt:
            # claude runs in its own session, so Ctrl-C does not reach it by itself.
            stop_group(proc)
            raise
        finally:
            kill_leftover_containers(demo / "work")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record and verify one headless demo session")
    parser.add_argument("--frontend", required=True, choices=FRONTENDS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-turns", type=int, default=60)
    parser.add_argument("--timeout-min", type=float, default=90)
    parser.add_argument("--record", action="store_true", help="file transcript and metrics row")
    args = parser.parse_args(argv)

    check_image()
    demo = Path(tempfile.mkdtemp(prefix=f"ss-demo-{args.frontend}.", dir="/tmp")).resolve()
    check_no_memory_files(demo)
    write_inputs(demo, args.frontend, args.model, args.max_turns)
    update_meta(
        demo,
        demo_id=demo.name,
        # verify_demo.py requires these: the runner checked every parent folder (above) and the
        # session environment switches Claude Code's memory files off.
        memory_isolation={
            "ancestors_clear": True,
            "claude_mds_disabled": clean_env().get("CLAUDE_CODE_DISABLE_CLAUDE_MDS") == "1",
        },
    )
    print(f"Demo folder: {demo}")
    print(
        f"Running {args.model} on the {args.frontend} front-end (up to {args.timeout_min} min)..."
    )
    code = run_session(demo, args.model, args.max_turns, args.timeout_min)
    update_meta(demo, claude_exit_code=code)
    if code is None:
        print("The session hit the time limit and was killed.")
    else:
        print(f"claude exited with code {code}.")

    verify_args = [str(demo)] + (["--record"] if args.record else [])
    status = verify_demo.main(verify_args)
    print(
        f"Files: {demo}/stream.jsonl (raw, keep out of the repo), debug.log, stderr.txt, "
        f"meta.json, verify/ (re-run copies), transcript.md"
    )
    return status


if __name__ == "__main__":
    sys.exit(main())
