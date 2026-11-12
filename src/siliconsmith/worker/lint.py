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


_CHECK_HEAD = re.compile(r"^(?:Warning|ERROR): (?P<msg>.*)$")
_CHECK_KINDS = (
    (re.compile(r"^multiple conflicting drivers for (?P<what>.+?):$"), "MULTIDRIVEN"),
    (re.compile(r"^found logic loop in module (?P<what>\S+?):$"), "LOGICLOOP"),
    (re.compile(r"^Wire (?P<what>.+?) is used but has no driver\.$"), "NODRIVER"),
)
_CHECK_SOURCE = re.compile(r"(?P<file>[\w./-]+\.v):(?P<line>\d+)")
_BIT_SUFFIX = re.compile(r"\s*\[\d+\]$")


def parse_yosys_check(text: str, default_file: str) -> list[dict[str, Any]]:
    """Findings from `yosys ... check -assert` output (the checks Verilator does not make).

    Yosys warns once per bit, so the findings are merged per signal. `MULTIDRIVEN` is a signal
    with more than one driver, `LOGICLOOP` a combinational loop and `NODRIVER` a used wire that
    nothing drives. The line comes from a `file:line` in the finding's own lines (a cell's
    source), or is 0. `ERROR: Found N problems` only gives a finding when no warning explains it.
    Each finding also carries `signal` (Yosys's name for the net, hierarchy prefix and all) and
    `source_file` (the file of that source line, as Yosys saw it, or "").
    """
    findings: dict[tuple[str, str], dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    error = ""
    for raw in text.splitlines():
        head = _CHECK_HEAD.match(raw)
        if head:
            current = None
            msg = head["msg"].strip()
            for pattern, code in _CHECK_KINDS:
                found = pattern.match(msg)
                if found:
                    what = _BIT_SUFFIX.sub("", found["what"].strip())
                    key = (code, what)
                    if key not in findings:
                        # `$not$a.v:3$1_Y` is Yosys's name for an internal net, not the agent's.
                        named = f"for {what}" if "$" not in what else "(a net driven twice)"
                        text_ = {
                            "MULTIDRIVEN": f"multiple conflicting drivers {named}",
                            "LOGICLOOP": f"combinational logic loop in module {what}",
                            "NODRIVER": f"wire {what} is used but has no driver",
                        }[code]
                        findings[key] = {
                            "severity": "error",
                            "code": code,
                            "file": default_file,
                            "line": 0,
                            "col": 0,
                            "msg": text_,
                            "detail": "",
                            "signal": what,
                            "source_file": "",
                        }
                    current = findings[key]
                    break
            else:
                if raw.startswith("ERROR: "):
                    error = error or msg
            sources = _CHECK_SOURCE.search(msg)
            if current is not None and sources and not current["line"]:
                current["line"] = int(sources["line"])
                current["source_file"] = sources["file"]
        elif current is not None and raw[:1] in (" ", "\t"):
            source = _CHECK_SOURCE.search(raw)
            if source and not current["line"]:
                current["line"] = int(source["line"])
                current["source_file"] = source["file"]
    out = list(findings.values())
    if not out and error:
        out.append(
            {
                "severity": "error",
                "code": "YOSYS_CHECK",
                "file": default_file,
                "line": 0,
                "col": 0,
                "msg": error,
                "detail": "",
            }
        )
    return out
