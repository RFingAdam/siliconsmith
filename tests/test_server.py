# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp.server import MCPServer

from siliconsmith import tools
from siliconsmith.context import ServerContext
from siliconsmith.server import RootError, build_server, resolve_root

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_list_tools_matches_registry(tmp_path: Path, mode: str) -> None:
    async with Client(build_server(tmp_path, None), mode=mode) as client:
        result = await client.list_tools()
    assert {tool.name for tool in result.tools} == set(tools.TOOL_MODULES)


async def test_instructions_are_short(tmp_path: Path) -> None:
    server = build_server(tmp_path, "verilog")
    assert server.instructions
    assert len(server.instructions) < 1000


async def test_instructions_only_name_existing_tools(tmp_path: Path) -> None:
    server = build_server(tmp_path, None)
    async with Client(server) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    mentioned = set(re.findall(r"\b([a-z][a-z_]*)\(", server.instructions or ""))
    assert mentioned <= names


async def test_stdio_entry_point(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "siliconsmith.server", "--root", str(tmp_path)]
    )
    async with Client(params) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}
    assert names == set(tools.TOOL_MODULES)


def test_register_all_calls_each_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[ServerContext] = []
    fake = types.ModuleType("siliconsmith.tools.fake")
    fake.register = lambda server, ctx: seen.append(ctx)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "siliconsmith.tools.fake", fake)
    monkeypatch.setattr(tools, "TOOL_MODULES", ("fake",))
    ctx = ServerContext(root=tmp_path, default_frontend="myhdl")
    tools.register_all(MCPServer("t"), ctx)
    assert seen == [ctx]


def test_root_explicit_wins(tmp_path: Path) -> None:
    env = {"SILICONSMITH_ROOT": "/nonexistent-a", "CLAUDE_PROJECT_DIR": "/nonexistent-b"}
    assert resolve_root(str(tmp_path), env) == tmp_path.resolve()


def test_root_env_order(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    both = {"SILICONSMITH_ROOT": str(a), "CLAUDE_PROJECT_DIR": str(b)}
    assert resolve_root(None, both) == a.resolve()
    assert resolve_root(None, {"CLAUDE_PROJECT_DIR": str(b)}) == b.resolve()


def test_root_must_be_a_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(RootError):
        resolve_root(str(missing), {})
    with pytest.raises(RootError, match="SILICONSMITH_ROOT"):
        resolve_root(None, {"SILICONSMITH_ROOT": str(missing)})
    with pytest.raises(RootError):
        resolve_root(None, {}, cwd=missing)


def test_empty_explicit_root_does_not_fall_back(tmp_path: Path) -> None:
    env = {"SILICONSMITH_ROOT": str(tmp_path)}
    with pytest.raises(RootError):
        resolve_root("", env)


def test_root_falls_back_to_cwd(tmp_path: Path) -> None:
    assert resolve_root(None, {}, cwd=tmp_path) == tmp_path.resolve()


def test_root_resolves_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert resolve_root(str(link), {}) == real.resolve()


def test_root_refuses_slash_and_home_from_env_or_cwd() -> None:
    with pytest.raises(RootError):
        resolve_root(None, {"SILICONSMITH_ROOT": "/"})
    with pytest.raises(RootError):
        resolve_root(None, {"CLAUDE_PROJECT_DIR": str(Path.home())})
    with pytest.raises(RootError):
        resolve_root(None, {}, cwd=Path.home())
    with pytest.raises(RootError):
        resolve_root(None, {}, cwd=Path("/"))


def test_root_allows_slash_and_home_when_explicit() -> None:
    assert resolve_root("/", {}) == Path("/")
    assert resolve_root(str(Path.home()), {}) == Path.home().resolve()


def test_server_main_refuses_home_root(monkeypatch: pytest.MonkeyPatch) -> None:
    from siliconsmith.server import main

    monkeypatch.setenv("SILICONSMITH_ROOT", str(Path.home()))
    with pytest.raises(SystemExit) as info:
        main([])
    assert info.value.code == 2
