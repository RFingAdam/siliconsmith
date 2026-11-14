# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The agent configuration snippets in README.md must work as written."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters

from siliconsmith import tools

README = Path(__file__).resolve().parent.parent / "README.md"

pytestmark = pytest.mark.anyio


def snippet(name: str) -> str:
    """The fenced code block that follows `<!-- snippet: name -->` in the README."""
    text = README.read_text(encoding="utf-8")
    match = re.search(rf"<!-- snippet: {re.escape(name)} -->\n```\w*\n(.*?)```", text, re.S)
    assert match, f"snippet {name!r} not found in README.md"
    return match.group(1)


def server_entry(config: dict[str, Any]) -> dict[str, Any]:
    servers = config["mcpServers"]
    assert list(servers) == ["siliconsmith"]
    entry: dict[str, Any] = servers["siliconsmith"]
    return entry


def child_env(extra: dict[str, str]) -> dict[str, str]:
    """The environment an agent would give the server, with this venv's bin dir on PATH.

    Under `uv run` the venv is already on PATH. Putting it there explicitly keeps the test
    working when pytest is started some other way.
    """
    env = dict(os.environ)
    env.pop("SILICONSMITH_ROOT", None)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env.update(extra)
    return env


async def list_tool_names(params: StdioServerParameters) -> set[str]:
    async with Client(params) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


async def test_claude_code_mcp_json_launches_and_lists_tools(tmp_path: Path) -> None:
    entry = server_entry(json.loads(snippet("claude-code-mcp-json")))
    assert entry["command"] == "siliconsmith-mcp"
    params = StdioServerParameters(
        command=entry["command"],
        args=entry.get("args", []),
        env=child_env({"CLAUDE_PROJECT_DIR": str(tmp_path)}),
        cwd=tmp_path,
    )
    assert await list_tool_names(params) == set(tools.TOOL_MODULES)


async def test_claude_code_server_uses_claude_project_dir(tmp_path: Path) -> None:
    entry = server_entry(json.loads(snippet("claude-code-mcp-json")))
    root = tmp_path / "workspace"
    root.mkdir()
    params = StdioServerParameters(
        command=entry["command"],
        args=entry.get("args", []),
        env=child_env({"CLAUDE_PROJECT_DIR": str(root)}),
        cwd=tmp_path,
    )
    async with Client(params) as client:
        result = await client.call_tool(
            "create_project",
            {"name": "adder", "frontend": "verilog", "author": "Ada", "description": "Adds."},
        )
    assert not result.is_error
    assert (root / "adder" / "siliconsmith.toml").is_file()


def test_claude_mcp_add_matches_mcp_json() -> None:
    words = shlex.split(snippet("claude-code-add"))
    assert words[:3] == ["claude", "mcp", "add"]
    assert "--" in words
    after = words[words.index("--") + 1 :]
    entry = server_entry(json.loads(snippet("claude-code-mcp-json")))
    assert after == [entry["command"], *entry.get("args", [])]
    assert "siliconsmith" in words[: words.index("--")]


async def test_cursor_json_structure_and_launch(tmp_path: Path) -> None:
    # Cursor's mcp.json format (cursor.com/docs/context/mcp): {"mcpServers": {name: {command,
    # args, env?}}}, with ${workspaceFolder} expanded by Cursor before it starts the server.
    config = json.loads(snippet("cursor-mcp-json"))
    assert set(config) == {"mcpServers"}
    entry = server_entry(config)
    assert set(entry) <= {"command", "args", "env", "envFile"}
    assert isinstance(entry["command"], str)
    assert isinstance(entry["args"], list) and all(isinstance(a, str) for a in entry["args"])
    assert entry["args"] == ["--root", "${workspaceFolder}"]
    # Expand the variable the way Cursor does, then check the server starts with that root.
    args = [a.replace("${workspaceFolder}", str(tmp_path)) for a in entry["args"]]
    params = StdioServerParameters(
        command=entry["command"], args=args, env=child_env({}), cwd=tmp_path
    )
    assert await list_tool_names(params) == set(tools.TOOL_MODULES)


def test_codex_toml() -> None:
    config = tomllib.loads(snippet("codex-config-toml"))
    entry = config["mcp_servers"]["siliconsmith"]
    assert entry["command"] == "siliconsmith-mcp"
    assert isinstance(entry["tool_timeout_sec"], int) and entry["tool_timeout_sec"] >= 600
    assert isinstance(entry["startup_timeout_sec"], int)
    assert entry["args"][0] == "--root"


def test_readme_has_no_personal_paths() -> None:
    text = README.read_text(encoding="utf-8")
    assert "/home/" not in text
    assert "/Users/" not in text
