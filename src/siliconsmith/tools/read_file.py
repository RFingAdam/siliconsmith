# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""`read_file`: read a text file from a project, optionally a range of lines."""

from __future__ import annotations

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from siliconsmith.context import ServerContext
from siliconsmith.paths import safe_read
from siliconsmith.result import ToolResult
from siliconsmith.tools._common import get_project

MAX_FILE_BYTES = 1024 * 1024  # largest file the host will open
MAX_RETURN_BYTES = 64 * 1024  # most text one call returns
BINARY_SNIFF = 8192

DESCRIPTION = """\
Read a text file from a project. path is relative to the project folder, for example \
'test/test.py' or 'hdl/counter.py'. The text is returned in details.content. Give start_line \
and end_line (1-based, inclusive) to read part of a file. At most 64 KiB of text is returned \
per call; when a file is longer the result is cut and next_hints says which lines to ask for \
next. Binary files are refused.
"""


def register(server: MCPServer, ctx: ServerContext) -> None:
    @server.tool(
        name="read_file",
        description=DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True),
    )
    def read_file(
        project: str, path: str, start_line: int | None = None, end_line: int | None = None
    ) -> ToolResult:
        proj = get_project(ctx, project)
        data = safe_read(proj.dir, path, MAX_FILE_BYTES)
        if b"\0" in data[:BINARY_SNIFF]:
            raise ToolError(
                f"{path!r} looks like a binary file (it contains NUL bytes); read_file only reads "
                "text. Use list_files to see its size."
            )
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"{path!r} is not valid UTF-8 text: {exc.reason}") from exc
        lines = text.splitlines(keepends=True)
        total = len(lines)
        first = 1 if start_line is None else start_line
        last = total if end_line is None else min(end_line, total)
        if first < 1 or (end_line is not None and end_line < first):
            raise ToolError(
                f"bad line range {start_line}-{end_line}; lines start at 1 and end_line must "
                "not be smaller than start_line"
            )
        if total and first > total:
            raise ToolError(f"start_line {first} is past the end of {path!r} ({total} lines)")
        chunk: list[str] = []
        used = 0
        for line in lines[first - 1 : last]:
            size = len(line.encode("utf-8"))
            if used + size > MAX_RETURN_BYTES and chunk:
                break
            chunk.append(line)
            used += size
        returned_last = first + len(chunk) - 1 if chunk else 0
        truncated = bool(chunk) and returned_last < last
        hints = []
        if truncated:
            hints.append(
                "Output was cut at 64 KiB. Call read_file again with "
                f"start_line={returned_last + 1}."
            )
        return ToolResult.from_lines(
            True,
            [f"{path}: lines {first if chunk else 0}-{returned_last} of {total}"],
            details={
                "path": path,
                "content": "".join(chunk),
                "start_line": first if chunk else 0,
                "end_line": returned_last,
                "total_lines": total,
                "truncated": truncated,
            },
            next_hints=hints,
        )
