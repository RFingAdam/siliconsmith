# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Parse `verilator --lint-only` output into findings.

Verilator prints one block per finding:

    %Warning-WIDTHTRUNC: counter.v:12:15: Operator ASSIGNW expects 4 bits ...
                                        : ... note: In instance 'w'
        12 |   assign y = a;
           |            ^
                          ... For warning description see https://verilator.org/warn/...

A finding starts at a `%Warning` or `%Error` line; the indented lines after it belong to it.
Stdlib only: this runs in the container, and the unit tests feed it real captured output.
"""

from __future__ import annotations

import re
from typing import Any

MAX_DETAIL_CHARS = 600
# Continuation lines that only repeat the source or point at the manual are left out of `detail`.
_NOISE = re.compile(
    r"^(\d+ \||\||\.\.\. (For (warning|error) description|Use \"/\*|See the manual))"
)
_HEAD = re.compile(
    r"^%(?P<sev>Warning|Error)(?:-(?P<code>[A-Za-z0-9_]+))?: "
    r"(?:(?P<file>[^\s:][^:]*):(?P<line>\d+):(?P<col>\d+): )?(?P<msg>.*)$"
)


def parse(text: str) -> list[dict[str, Any]]:
    """Return findings as `{severity, code, file, line, col, msg, detail}`.

    `severity` is `warning` or `error`. A finding without a code gets `SYNTAX` (for a syntax
    error) or `ERROR`; without a position it gets `file` "" and `line`/`col` 0. `detail` joins
    the continuation lines that add something (notes, locations of related code). The closing
    `%Error: Exiting due to N warning(s)` line is not a finding.
    """
    findings: list[dict[str, Any]] = []
    detail: list[str] = []
    current: list[dict[str, Any]] = []  # the finding that continuation lines attach to

    def close() -> None:
        if current and detail:
            current[0]["detail"] = "\n".join(detail)[:MAX_DETAIL_CHARS]
        detail.clear()
        current.clear()

    for raw in text.splitlines():
        match = _HEAD.match(raw)
        if match:
            close()
            msg = match["msg"].strip()
            if msg.startswith("Exiting due to"):
                continue
            code = match["code"] or ("SYNTAX" if msg.startswith("syntax error") else "ERROR")
            current.append(
                {
                    "severity": match["sev"].lower(),
                    "code": code,
                    "file": match["file"] or "",
                    "line": int(match["line"] or 0),
                    "col": int(match["col"] or 0),
                    "msg": msg,
                    "detail": "",
                }
            )
            findings.append(current[0])
        elif raw[:1] in (" ", "\t") and raw.strip() and current:
            text = raw.strip()
            if not _NOISE.match(text):
                detail.append(text.removeprefix(": "))
        elif not raw.strip():
            continue
        else:
            close()  # banner lines such as "- V e r i l a t i o n   R e p o r t"
    close()
    return findings
