# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Helpers shared by the project tools. This module registers no tool."""

from __future__ import annotations

import ast

from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.context import ServerContext
from siliconsmith.paths import Category, atomic_write, classify_write, project_lock, safe_read
from siliconsmith.project import Project, load_project
from siliconsmith.result import Artifact, ToolResult
from siliconsmith.spec import MAX_SPEC_BYTES, SPEC_NAME, Spec, parse_spec

MAX_WRITE_BYTES = 256 * 1024

# Which tool writes which category of file.
TOOL_FOR: dict[Category, str] = {
    "rtl": "write_rtl",
    "doc": "write_rtl",
    "test": "write_test",
    "spec": "write_spec",
}


def get_project(ctx: ServerContext, name: str) -> Project:
    return load_project(ctx.root, name)


def read_spec_ids(project: Project) -> list[str] | None:
    """Requirement ids of the project's valid spec.yaml, or None if it is missing or invalid."""
    with project_lock(project.dir):
        return read_spec_ids_locked(project)


def read_spec_ids_locked(project: Project) -> list[str] | None:
    """Like `read_spec_ids`, for a caller that already holds the project lock."""
    try:
        text = safe_read(project.dir, SPEC_NAME, MAX_SPEC_BYTES).decode("utf-8")
    except (ToolError, UnicodeDecodeError):
        return None
    try:
        parsed = parse_spec(text)
    except Exception:  # a spec that cannot be checked counts as no valid spec
        return None
    return parsed.requirement_ids() if isinstance(parsed, Spec) else None


def write_text_file(
    ctx: ServerContext,
    *,
    tool: str,
    accepts: frozenset[Category],
    project_name: str,
    path: str,
    content: str,
) -> ToolResult:
    """Shared body of `write_rtl` and `write_test`: check, write atomically, syntax-check .py."""
    project = get_project(ctx, project_name)
    category = classify_write(project.name, project.frontend, path)
    if category not in accepts:
        right = TOOL_FOR[category]
        raise ToolError(f"{path!r} is a {category} file; write it with {right}, not {tool}")
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ToolError(f"content is not valid UTF-8 text: {exc.reason}") from exc
    if b"\0" in data:
        raise ToolError("content contains a NUL byte; only text files can be written")
    if len(data) > MAX_WRITE_BYTES:
        raise ToolError(
            f"content is {len(data)} bytes; the limit is {MAX_WRITE_BYTES} bytes. "
            "Split the design into several files."
        )
    with project_lock(project.dir):
        atomic_write(project.dir, path, data)
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    details: dict[str, object] = {
        "path": path,
        "category": category,
        "bytes": len(data),
        "lines": lines,
    }
    artifacts = [Artifact(path=path, kind=category)]
    head = f"Wrote {path} ({len(data)} bytes, {lines} lines)."
    if not path.endswith(".py"):
        return ToolResult.from_lines(True, [head], details=details, artifacts=artifacts)
    try:
        ast.parse(content, filename=path)
    except (SyntaxError, RecursionError, MemoryError) as exc:
        if isinstance(exc, SyntaxError):
            line, col = exc.lineno or 0, exc.offset or 0
            message = f"{path}:{line}:{col}: SyntaxError: {exc.msg}"
            details["syntax_error"] = {"file": path, "line": line, "col": col, "msg": exc.msg}
        else:
            message = f"{path}: could not be parsed ({type(exc).__name__})"
            details["syntax_error"] = {"file": path, "line": 0, "col": 0, "msg": message}
        details["syntax_ok"] = False
        return ToolResult.from_lines(
            False,
            [head, "Python syntax check failed (the file was saved anyway):", message],
            details=details,
            artifacts=artifacts,
            next_hints=[f"Fix the syntax error and call {tool} again with the full {path}."],
        )
    details["syntax_ok"] = True
    return ToolResult.from_lines(
        True, [head, "Python syntax check passed."], details=details, artifacts=artifacts
    )
