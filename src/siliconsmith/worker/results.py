# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Read cocotb's JUnit `results.xml` into compact test records.

Each record is `{name, outcome, sim_time_ns, message, file, line, assertion, expected, actual}`.
cocotb 2.x rewrites `assert` statements in test modules, so a failure reads like
`assert 255 == 0` (left side = what the design produced, right side = what the test wanted).
A test can also carry its own wording, such as `expected 10, got 0`; it is used only when
there is no rewritten `==` comparison.

The file is written by cocotb inside the simulator process that runs the agent's test code, so it
is treated as untrusted: no DOCTYPE or entity (no expansion tricks), a size cap, and every text
field is clipped. Stdlib only: this runs in the container, and the unit tests feed it files
captured from the real image.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from siliconsmith.worker.limits import (
    MAX_TESTS,
    TEST_NAME_MAX,
    TEST_TEXT_MAX,
    TEST_VALUE_MAX,
    clip,
)

MAX_XML_BYTES = 8 * 1024 * 1024
TEST_PREFIX = "/work/test/"
_UNIT_NS = {"fs": 1e-6, "ps": 1e-3, "ns": 1.0, "us": 1e3, "ms": 1e6, "s": 1e9}

_FRAME = re.compile(r'^\s*File "([^"]+)", line (\d+)(?:, in (\S+))?')
_EXC_LINE = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Failure|Timeout|Exit|Interrupt)\w*)(?::\s?(.*))?$"
)
_LOGIC_ARRAY = re.compile(r"^(?:cocotb\.types\.)?LogicArray\('([^']*)'")
# One value token: a bracketed group, a quoted string, or a run without spaces or separators.
# Single tokens keep prose such as "expected the counter to wrap but it was still counting" from
# being read as a comparison.
_VALUE = r"(?:[(\[{][^)\]}]*[)\]}]|'[^']*'|\"[^\"]*\"|[^\s,;:]+)"
_SAW = r"(?:got|actual|was|saw|found|received)"
_EXPECTED_GOT = re.compile(
    rf"expected\s*[:=]?\s*(?P<e>{_VALUE})\s*[,;:]?\s+(?:but\s+)?{_SAW}\s*[:=]?\s*"
    rf"(?P<a>{_VALUE})[\s.]*$",
    re.IGNORECASE,
)
_GOT_EXPECTED = re.compile(
    rf"{_SAW}\s*[:=]?\s*(?P<a>{_VALUE})\s*[,;:]?\s+(?:but\s+)?expected\s*[:=]?\s*"
    rf"(?P<e>{_VALUE})[\s.]*$",
    re.IGNORECASE,
)


class ResultsError(ValueError):
    """results.xml is missing, too large, or not the plain XML cocotb writes."""


def _normalize(value: str) -> str:
    """`LogicArray('00001010', ...)` -> `10`; everything else is trimmed and clipped."""
    value = value.strip()
    match = _LOGIC_ARRAY.match(value)
    if match:
        bits = match.group(1)
        value = str(int(bits, 2)) if bits and set(bits) <= {"0", "1"} else bits
    return clip(value, TEST_VALUE_MAX)


def _split_equal(expr: str) -> tuple[str, str] | None:
    """Split `L == R` at the first top-level `==` (not inside brackets or quotes)."""
    depth = 0
    quote = ""
    i = 0
    while i < len(expr):
        ch = expr[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and expr.startswith("==", i):
            left, right = expr[:i].strip(), expr[i + 2 :].strip()
            return (left, right) if left and right else None
        i += 1
    return None


def _from_message(message: str) -> tuple[str, str] | None:
    """`(expected, actual)` from wording such as `expected 10, got 0`."""
    match = _EXPECTED_GOT.search(message)
    if match:
        return _normalize(match.group("e")), _normalize(match.group("a"))
    match = _GOT_EXPECTED.search(message)
    if match:
        return _normalize(match.group("e")), _normalize(match.group("a"))
    return None


def analyze_failure(text: str) -> dict[str, Any]:
    """Pull `{file, line, assertion, message, expected, actual}` out of a failure's traceback.

    The deepest frame in a file under /work/test is the failing assertion. Missing parts are None.
    """
    lines = text.splitlines()
    file: str | None = None
    line_no: int | None = None
    assertion: str | None = None
    after = 0
    for index, raw in enumerate(lines):
        frame = _FRAME.match(raw)
        if frame and frame.group(1).startswith(TEST_PREFIX):
            file = frame.group(1)[len("/work/") :]
            line_no = int(frame.group(2))
            nxt = lines[index + 1] if index + 1 < len(lines) else ""
            assertion = nxt.strip() if nxt.strip() and not _FRAME.match(nxt) else None
            after = index + 2 if assertion else index + 1
    message: str | None = None
    exc_text = ""
    rewritten: str | None = None
    for index in range(after, len(lines)):
        match = _EXC_LINE.match(lines[index])
        if not match:
            continue
        exc_text = (match.group(2) or "").strip()
        head = f"{match.group(1)}: {exc_text}" if exc_text else match.group(1)
        message = clip(head, TEST_TEXT_MAX)
        if exc_text.startswith("assert "):
            rewritten = exc_text
            exc_text = ""
        else:
            for later in lines[index + 1 : index + 8]:
                if later.startswith("assert "):
                    rewritten = later.strip()
                    break
        break
    expected = actual = None
    # The rewritten `assert L == R` holds the real values, so it wins over the message's wording.
    sides = _split_equal(rewritten[len("assert ") :]) if rewritten is not None else None
    if sides is not None:
        actual, expected = _normalize(sides[0]), _normalize(sides[1])
    else:
        found = _from_message(exc_text) if exc_text else None
        if found is not None:
            expected, actual = found
    if message and rewritten and message.endswith(rewritten[: TEST_TEXT_MAX // 2]):
        message = None  # a bare assert: the assertion text says it all
    return {
        "file": clip(file, TEST_NAME_MAX) if file else None,
        "line": line_no,
        "assertion": clip(assertion, TEST_TEXT_MAX) if assertion else None,
        "message": message,
        "expected": expected,
        "actual": actual,
    }


def _sim_time_ns(case: ET.Element) -> float | None:
    props = {p.get("name"): p.get("value") for p in case.iter("property")}
    raw = props.get("sim_time_duration")
    unit = _UNIT_NS.get(props.get("sim_time_unit") or "ns")
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    if value is None or unit is None or value != value or value < 0:
        return None
    return round(value * unit, 3)


def _record(case: ET.Element) -> dict[str, Any]:
    name = clip(case.get("name") or "", TEST_NAME_MAX)
    outcome = "pass"
    detail: ET.Element | None = None
    for child in case:
        if child.tag in ("failure", "error", "skipped"):
            outcome = {"failure": "fail", "error": "error", "skipped": "skip"}[child.tag]
            detail = child
            break
    record: dict[str, Any] = {
        "name": name,
        "module": clip(case.get("classname") or "", TEST_NAME_MAX),
        "outcome": outcome,
        "sim_time_ns": _sim_time_ns(case),
        "message": None,
        "file": None,
        "line": None,
        "assertion": None,
        "expected": None,
        "actual": None,
    }
    if detail is not None and outcome in ("fail", "error"):
        text = (detail.text or "") or (detail.get("message") or "")
        record.update(analyze_failure(text))
        if record["message"] is None and not record["assertion"]:
            first = (detail.get("message") or text).strip().splitlines()
            record["message"] = clip(first[0], TEST_TEXT_MAX) if first else None
    return record


def parse_junit(data: bytes) -> list[dict[str, Any]]:
    """Test records from the bytes of a cocotb results.xml (at most MAX_TESTS)."""
    if len(data) > MAX_XML_BYTES:
        raise ResultsError(f"results.xml is larger than {MAX_XML_BYTES} bytes")
    try:
        lowered = data.decode("utf-8").lower()
    except UnicodeDecodeError as exc:  # also keeps a UTF-16 file from hiding a DOCTYPE
        raise ResultsError("results.xml is not UTF-8 text") from exc
    if "<!doctype" in lowered or "<!entity" in lowered:
        raise ResultsError("results.xml has a DOCTYPE or ENTITY declaration")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ResultsError(f"results.xml is not valid XML ({exc})") from exc
    records = [_record(case) for case in root.iter("testcase")]
    return records[:MAX_TESTS]
