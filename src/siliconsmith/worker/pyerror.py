# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Turn a Python exception from the agent's design file into `{file, line, col, msg, source}`.

The file is mapped to a project-relative path (`/work/hdl/x.py` becomes `hdl/x.py`), so the agent
can open it with its own tools. Stdlib only: this runs in the container, and the unit tests run
it on the host with made-up exceptions.
"""

from __future__ import annotations

import linecache
import re
import traceback
from typing import Any

WORK = "/work"
HDL_PREFIX = f"{WORK}/hdl/"
MAX_MESSAGE_LINES = 8
MAX_MESSAGE_CHARS = 600
MAX_SOURCE_CHARS = 200

# Where else a message can name a location: `x.py:12`, `in file x.py, line 12` (MyHDL's
# conversion errors) and `File "x.py", line 12` (a formatted traceback in the text).
_LOCATION_RES = (
    re.compile(r"(\S+?\.py):(\d+)"),
    re.compile(r"in file (\S+?), line (\d+)"),
    re.compile(r"""File ["'](\S+?)["'], line (\d+)"""),
)


def to_project_path(path: str) -> str:
    """`/work/hdl/x.py` -> `hdl/x.py`. Other paths are returned as they are."""
    return path[len(WORK) + 1 :] if path.startswith(WORK + "/") else path


def map_paths(text: str) -> str:
    """Replace every `/work/` in a message with nothing, so container paths read as project ones."""
    return text.replace(WORK + "/", "")


def _clip(text: str, lines: int = MAX_MESSAGE_LINES, chars: int = MAX_MESSAGE_CHARS) -> str:
    kept = text.strip().splitlines()[:lines]
    out = "\n".join(kept)
    return out if len(out) <= chars else out[: chars - 1] + "…"


def _source_line(path: str, line: int | None) -> str | None:
    if not line or line < 1:
        return None
    text = linecache.getline(path, line).strip()
    return text[:MAX_SOURCE_CHARS] if text else None


def _from_message(message: str) -> tuple[str, int] | None:
    """The first `file:line` in a message, preferring files under /work/hdl."""
    found: list[tuple[str, int]] = []
    for pattern in _LOCATION_RES:
        found += [(m.group(1), int(m.group(2))) for m in pattern.finditer(message)]
    for path, line in found:
        if path.startswith(HDL_PREFIX):
            return path, line
    return found[0] if found else None


def extract(exc: BaseException, default_file: str) -> dict[str, Any]:
    """Describe `exc` as a dict with `kind`, `file`, `line`, `col`, `msg` and `source`.

    * A SyntaxError carries its own file, line and column.
    * Any other exception: the deepest traceback frame inside `/work/hdl`.
    * Otherwise a `file:line` found in the message; failing that, `default_file` without a line.
    """
    kind = type(exc).__name__
    if isinstance(exc, SyntaxError):
        path = exc.filename or default_file
        line = exc.lineno
        source = (exc.text or "").strip() or _source_line(path, line)
        return {
            "kind": kind,
            "file": to_project_path(path),
            "line": line,
            "col": exc.offset,
            "msg": _clip(map_paths(f"{kind}: {exc.msg or 'invalid syntax'}")),
            "source": source[:MAX_SOURCE_CHARS] if source else None,
        }
    message = f"{kind}: {exc}" if str(exc) else kind
    path, line = default_file, None
    source = None
    frames = [
        f for f in traceback.extract_tb(exc.__traceback__) if f.filename.startswith(HDL_PREFIX)
    ]
    if frames:
        frame = frames[-1]
        path, line = frame.filename, frame.lineno
        source = (frame.line or "").strip() or None
    else:
        located = _from_message(str(exc))
        if located is not None:
            path, line = located
            source = _source_line(path, line)
    return {
        "kind": kind,
        "file": to_project_path(path),
        "line": line,
        "col": None,
        "msg": _clip(map_paths(message)),
        "source": source[:MAX_SOURCE_CHARS] if source else None,
    }


def design_error(file: str, msg: str) -> dict[str, Any]:
    """The same shape for an error that is not an exception (no `top()`, bad parameters, ...)."""
    return {
        "kind": "DesignError",
        "file": file,
        "line": None,
        "col": None,
        "msg": _clip(msg),
        "source": None,
    }


def format_lines(error: dict[str, Any]) -> list[str]:
    """A short, human-readable rendering: `file:line:col: message` and the source line."""
    where = str(error["file"])
    if error.get("line"):
        where += f":{error['line']}"
        if error.get("col"):
            where += f":{error['col']}"
    head, *rest = str(error["msg"]).splitlines() or [""]
    lines = [f"{where}: {head}", *rest]
    if error.get("source"):
        lines.append(f"    {error['source']}")
    return lines
