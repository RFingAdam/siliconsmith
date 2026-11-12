# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from siliconsmith.result import MAX_SUMMARY_CHARS, MAX_SUMMARY_LINES
from siliconsmith.server import INSTRUCTIONS, build_server
from siliconsmith.spec import Spec, parse_spec

pytestmark = pytest.mark.anyio

ARGS = {"author": "Ada Lovelace", "description": "Adds two numbers."}


async def call(client: Client, tool: str, **args: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert not result.is_error, result.content
    assert result.structured_content is not None
    data: dict[str, Any] = result.structured_content
    assert set(data) == {"ok", "summary", "details", "artifacts", "next_hints"}
    assert len(data["summary"].splitlines()) <= MAX_SUMMARY_LINES
    assert len(data["summary"]) <= MAX_SUMMARY_CHARS
    return data


async def fail(client: Client, tool: str, **args: Any) -> str:
    result = await client.call_tool(tool, args)
    assert result.is_error
    return str(result.content[0].text)  # type: ignore[union-attr]


@pytest.fixture
def server_client(tmp_path: Path) -> Client:
    return Client(build_server(tmp_path, "verilog"))


async def test_tools_listed_with_project_first(server_client: Client) -> None:
    async with server_client as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {
        "create_project",
        "list_files",
        "read_file",
        "write_rtl",
        "write_test",
        "write_spec",
    }
    for name in ("list_files", "read_file", "write_rtl", "write_test", "write_spec"):
        assert next(iter(tools[name].input_schema["properties"])) == "project"
        assert tools[name].description


async def test_create_project_default_and_explicit_frontend(
    tmp_path: Path, server_client: Client
) -> None:
    async with server_client as client:
        data = await call(client, "create_project", name="adder", **ARGS)
        assert data["ok"] and data["details"]["frontend"] == "verilog"
        assert data["details"]["source"] == "src/adder.v"
        data = await call(client, "create_project", name="amp", frontend="amaranth", **ARGS)
        assert data["details"]["source"] == "hdl/amp.py"
    assert (tmp_path / "adder" / "src" / "project.v").is_file()
    assert (tmp_path / "amp" / "hdl" / "amp.py").is_file()


async def test_create_project_without_default_needs_frontend(tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, None)) as client:
        message = await fail(client, "create_project", name="adder", **ARGS)
        assert "amaranth, myhdl, verilog" in message
        message = await fail(client, "create_project", name="adder", frontend="vhdl", **ARGS)
        assert "amaranth, myhdl, verilog" in message
    assert list(tmp_path.iterdir()) == []


async def test_create_project_errors(server_client: Client) -> None:
    async with server_client as client:
        assert "invalid project name" in await fail(client, "create_project", name="module", **ARGS)
        await call(client, "create_project", name="adder", **ARGS)
        assert "already exists" in await fail(client, "create_project", name="adder", **ARGS)


async def test_list_and_read_files(server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        listing = await call(client, "list_files", project="adder")
        paths = [f["path"] for f in listing["details"]["files"]]
        assert "src/adder.v" in paths and "test/test.py" in paths and "src/project.v" in paths
        assert not any(p.startswith(("build/", ".")) for p in paths)
        data = await call(client, "read_file", project="adder", path="src/adder.v")
        assert "module adder (" in data["details"]["content"]
        part = await call(
            client, "read_file", project="adder", path="src/adder.v", start_line=1, end_line=2
        )
        assert part["details"]["end_line"] == 2
        assert part["details"]["content"].count("\n") == 2
        assert part["details"]["total_lines"] > 2


async def test_list_files_include_build(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        (tmp_path / "adder" / "build" / "log.txt").write_text("x")
        hidden = await call(client, "list_files", project="adder")
        shown = await call(client, "list_files", project="adder", include_build=True)
    assert "build/log.txt" not in [f["path"] for f in hidden["details"]["files"]]
    assert "build/log.txt" in [f["path"] for f in shown["details"]["files"]]


async def test_read_file_errors(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        (tmp_path / "adder" / "build" / "blob.bin").write_bytes(b"\x00\x01\x02")
        (tmp_path / "adder" / "build" / "latin.txt").write_bytes(b"caf\xe9\n")
        assert "binary" in await fail(client, "read_file", project="adder", path="build/blob.bin")
        assert "UTF-8" in await fail(client, "read_file", project="adder", path="build/latin.txt")
        assert "'..'" in await fail(client, "read_file", project="adder", path="../adder/x")
        assert "does not exist" in await fail(client, "read_file", project="adder", path="nope")
        assert "bad line range" in await fail(
            client, "read_file", project="adder", path="src/adder.v", start_line=5, end_line=2
        )
        assert "past the end" in await fail(
            client, "read_file", project="adder", path="src/adder.v", start_line=999
        )


async def test_read_file_truncates_large_output(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        big = "".join(f"line {i:05d} {'x' * 70}\n" for i in range(2000))
        (tmp_path / "adder" / "docs" / "big.md").write_text(big)
        data = await call(client, "read_file", project="adder", path="docs/big.md")
    assert data["details"]["truncated"]
    assert len(data["details"]["content"].encode()) <= 64 * 1024
    assert "start_line=" in data["next_hints"][0]


async def test_unknown_project_lists_existing(server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        for tool, extra in (
            ("list_files", {}),
            ("read_file", {"path": "x"}),
            ("write_rtl", {"path": "src/x.v", "content": ""}),
            ("write_test", {"path": "test/x.py", "content": ""}),
        ):
            message = await fail(client, tool, project="nope", **extra)
            assert "unknown project 'nope'" in message and "adder" in message


async def test_write_rtl_verilog_and_overwrite(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        code = "module adder(); endmodule\n"
        data = await call(client, "write_rtl", project="adder", path="src/adder.v", content=code)
        assert data["ok"] and data["artifacts"] == [{"path": "src/adder.v", "kind": "rtl"}]
    assert (tmp_path / "adder" / "src" / "adder.v").read_text() == code


async def test_write_rtl_python_syntax_check(tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, "amaranth")) as client:
        await call(client, "create_project", name="amp", **ARGS)
        good = await call(client, "write_rtl", project="amp", path="hdl/amp.py", content="x = 1\n")
        assert good["ok"] and good["details"]["syntax_ok"]
        bad = await call(
            client, "write_rtl", project="amp", path="hdl/amp.py", content="x = 1\ndef (:\n"
        )
        assert not bad["ok"]
        assert "hdl/amp.py:2:" in bad["summary"] and "SyntaxError" in bad["summary"]
        assert bad["next_hints"]
        assert bad["details"]["syntax_error"]["line"] == 2
        # Saved even though it does not parse; and it was never executed.
        assert (tmp_path / "amp" / "hdl" / "amp.py").read_text() == "x = 1\ndef (:\n"
        helper = await call(
            client, "write_rtl", project="amp", path="hdl/helpers/alu.py", content="y = 2\n"
        )
        assert helper["ok"]


async def test_write_rtl_does_not_execute_python(tmp_path: Path) -> None:
    marker = tmp_path / "ran.txt"
    code = f"open({str(marker)!r}, 'w').write('x')\n"
    async with Client(build_server(tmp_path, "myhdl")) as client:
        await call(client, "create_project", name="amp", **ARGS)
        await call(client, "write_rtl", project="amp", path="hdl/amp.py", content=code)
        await call(client, "write_test", project="amp", path="test/test.py", content=code)
    assert not marker.exists()
    ast.parse(code)


async def test_write_test(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        data = await call(
            client, "write_test", project="adder", path="test/test.py", content="x = 1\n"
        )
        assert data["ok"] and data["artifacts"][0]["kind"] == "test"
    assert (tmp_path / "adder" / "test" / "test.py").read_text() == "x = 1\n"


async def test_wrong_tool_names_the_right_one(server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        message = await fail(
            client, "write_rtl", project="adder", path="test/test.py", content="x = 1\n"
        )
        assert "write_test" in message and "test/test.py" in message
        message = await fail(client, "write_test", project="adder", path="src/adder.v", content="x")
        assert "write_rtl" in message
        message = await fail(client, "write_rtl", project="adder", path="spec.yaml", content="x")
        assert "write_spec" in message and "spec.yaml" in message
        message = await fail(client, "write_test", project="adder", path="spec.yaml", content="x")
        assert "write_spec" in message


async def test_write_rejects_unsafe_and_forbidden_paths(
    tmp_path: Path, server_client: Client
) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        original = (tmp_path / "adder" / "src" / "project.v").read_bytes()
        for tool, path in [
            ("write_rtl", "../evil.v"),
            ("write_rtl", "/tmp/evil.v"),
            ("write_rtl", "src/ev\0il.v"),
            ("write_rtl", "src\\adder.v"),
            ("write_rtl", "src/project.v"),
            ("write_rtl", "siliconsmith.toml"),
            ("write_rtl", "info.yaml"),
            ("write_rtl", "docs/verification.md"),
            ("write_rtl", ".github/workflows/x.md"),
            ("write_test", "test/Makefile"),
            ("write_test", "test/conftest.py"),
            ("write_test", "test/x.mk"),
            ("write_test", "test/.hidden.py"),
            ("write_test", "build/x.py"),
        ]:
            result = await client.call_tool(
                tool, {"project": "adder", "path": path, "content": "x = 1\n"}
            )
            assert result.is_error, (tool, path)
    assert (tmp_path / "adder" / "src" / "project.v").read_bytes() == original
    assert not (tmp_path.parent / "evil.v").exists()


async def test_write_refuses_symlinked_target(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        (tmp_path / "adder" / "test" / "evil.py").symlink_to(victim)
        message = await fail(
            client, "write_test", project="adder", path="test/evil.py", content="x = 1\n"
        )
        assert "not a regular file" in message
    assert victim.read_text() == "keep"


async def test_write_size_and_encoding_limits(server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        too_big = "a" * (256 * 1024 + 1)
        assert "limit" in await fail(
            client, "write_rtl", project="adder", path="src/adder.v", content=too_big
        )
        assert "NUL" in await fail(
            client, "write_rtl", project="adder", path="src/adder.v", content="a\0b"
        )
        ok = await call(client, "write_rtl", project="adder", path="src/adder.v", content="// ü\n")
        assert ok["ok"]


# --- spec and Covers (M0-7) --------------------------------------------------------------------

SPEC = """\
# my spec
title: Counter
summary: Counts.
clock_hz: 5000000
reset: none
pins:
  ui:
    0: enable
requirements:
  - id: R1
    text: Counts up.
  - id: R2
    text: Holds when disabled.
"""


async def test_create_project_writes_a_valid_stub_spec(
    tmp_path: Path, server_client: Client
) -> None:
    async with server_client as client:
        data = await call(client, "create_project", name="adder", **ARGS)
    assert "spec.yaml" in data["details"]["files"]
    spec = parse_spec((tmp_path / "adder" / "spec.yaml").read_text())
    assert isinstance(spec, Spec) and spec.requirement_ids() == ["R1"]


async def test_write_spec_saves_valid_spec_exactly(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        data = await call(client, "write_spec", project="adder", content=SPEC)
    assert data["ok"] and data["artifacts"] == [{"path": "spec.yaml", "kind": "spec"}]
    assert data["details"]["requirement_ids"] == ["R1", "R2"]
    assert "Counter" in data["summary"] and "5000000" in data["summary"]
    assert "Covers:" in data["next_hints"][0]
    assert (tmp_path / "adder" / "spec.yaml").read_text() == SPEC  # comments kept


async def test_write_spec_invalid_is_not_written(tmp_path: Path, server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        before = (tmp_path / "adder" / "spec.yaml").read_bytes()
        bad = SPEC.replace("clock_hz: 5000000\n", "").replace("id: R2", "id: two")
        bad += "colour: red\n"
        data = await call(client, "write_spec", project="adder", content=bad)
        assert not data["ok"]
        assert {e["path"] for e in data["details"]["errors"]} == {
            "clock_hz",
            "requirements[1].id",
            "colour",
        }
        assert "clock_hz: required field is missing" in data["summary"]
        assert "NOT saved" in data["summary"]
        garbage = await call(client, "write_spec", project="adder", content="a: [")
        assert not garbage["ok"] and garbage["details"]["errors"][0]["path"] == "yaml"
    assert (tmp_path / "adder" / "spec.yaml").read_bytes() == before


async def test_write_spec_unknown_project(server_client: Client) -> None:
    async with server_client as client:
        assert "unknown project" in await fail(client, "write_spec", project="nope", content="x")


async def test_write_test_reports_covers_and_warnings(
    tmp_path: Path, server_client: Client
) -> None:
    marker = tmp_path / "ran.txt"
    code = (
        f"open({str(marker)!r}, 'w').write('x')\n"
        "import cocotb\n"
        "@cocotb.test()\nasync def test_a(dut):\n    '''Covers: R1'''\n"
        "@cocotb.test()\nasync def test_b(dut):\n    '''Covers: R1, R7'''\n"
        "@cocotb.test()\nasync def test_c(dut):\n    pass\n"
    )
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        await call(client, "write_spec", project="adder", content=SPEC)
        data = await call(client, "write_test", project="adder", path="test/test.py", content=code)
    assert data["ok"]
    assert data["details"]["covers"] == {"test_a": ["R1"], "test_b": ["R1", "R7"], "test_c": []}
    warnings = data["details"]["covers_warnings"]
    assert any("unknown requirement R7" in w for w in warnings)
    assert any("test_c" in w and "no 'Covers:'" in w for w in warnings)
    assert "test_a -> R1" in data["summary"] and "Warning:" in data["summary"]
    assert not marker.exists()  # the test file was parsed, never run


async def test_write_test_without_valid_spec_skips_id_check(
    tmp_path: Path, server_client: Client
) -> None:
    code = "import cocotb\n@cocotb.test()\nasync def t(dut):\n    '''Covers: R9'''\n"
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        (tmp_path / "adder" / "spec.yaml").write_text("not: valid\n")
        data = await call(client, "write_test", project="adder", path="test/test.py", content=code)
    assert data["ok"] and data["details"]["covers"] == {"t": ["R9"]}
    assert data["details"]["covers_warnings"] == []
    assert "not checked" in data["summary"]


async def test_write_test_syntax_error_keeps_the_syntax_report(server_client: Client) -> None:
    async with server_client as client:
        await call(client, "create_project", name="adder", **ARGS)
        data = await call(
            client, "write_test", project="adder", path="test/test.py", content="def (:\n"
        )
    assert not data["ok"] and "SyntaxError" in data["summary"]


def test_instructions_put_the_spec_first() -> None:
    assert INSTRUCTIONS.index("write_spec") < INSTRUCTIONS.index("write_test")
    assert INSTRUCTIONS.index("write_test") < INSTRUCTIONS.index("write_rtl")
    assert len(INSTRUCTIONS.encode()) <= 4096
