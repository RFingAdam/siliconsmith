# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Under section 7 of the GNU AGPL version 3, you are granted the additional
# permission in OUTPUT-EXCEPTION.md: files this module writes into a user's
# project, and the text it copies into them, may be used under Apache-2.0.

"""Read `Covers:` tags from cocotb test docstrings, and work out which requirements are covered.

`parse_covers` uses `ast` only. It never imports or runs the test file, because the file was
written by an agent. A test says which requirements it checks with a docstring line such as
`Covers: R1, R2`. `coverage` takes the outcomes of real test runs: only a passing test counts.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

REQ_ID_RE = re.compile(r"^R[1-9][0-9]*$")
_COVERS_LINE = re.compile(r"^\s*Covers:(.*)$")
_COVERS_ANY_CASE = re.compile(r"^\s*covers\s*:", re.IGNORECASE)


@dataclass(frozen=True)
class CocotbTest:
    name: str
    covers: tuple[str, ...]
    skipped_static: bool
    line: int
    # expect_fail=True or expect_error=...: cocotb reports a failing such test as PASSED, so it
    # must never count as coverage, whatever its outcome says.
    expects_failure: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "covers": list(self.covers),
            "skipped_static": self.skipped_static,
            "line": self.line,
            "expects_failure": self.expects_failure,
        }


@dataclass(frozen=True)
class CoversReport:
    tests: list[CocotbTest] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Set when the file does not parse: {"file", "line", "col", "msg"}.
    error: dict[str, Any] | None = None

    def covered_ids(self) -> list[str]:
        """Ids named by tests that are not statically skipped (a claim; `coverage` needs runs)."""
        seen: dict[str, None] = {}
        for test in self.tests:
            if not test.skipped_static and not test.expects_failure:
                seen.update(dict.fromkeys(test.covers))
        return list(seen)


def _cocotb_names(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Names that stand for the `cocotb` module, and names that stand for `cocotb.test`."""
    modules = {"cocotb"}
    tests: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cocotb" and alias.asname:
                    modules.add(alias.asname)
        elif isinstance(node, ast.ImportFrom) and node.module == "cocotb" and node.level == 0:
            for alias in node.names:
                if alias.name == "test":
                    tests.add(alias.asname or "test")
    return modules, tests


def _is_test_decorator(dec: ast.expr, modules: set[str], tests: set[str]) -> ast.Call | bool:
    """Return the decorator call, or True for a bare `@cocotb.test`, or False if not a test."""
    target = dec.func if isinstance(dec, ast.Call) else dec
    is_test = (
        isinstance(target, ast.Attribute)
        and target.attr == "test"
        and isinstance(target.value, ast.Name)
        and target.value.id in modules
    ) or (isinstance(target, ast.Name) and target.id in tests)
    if not is_test:
        return False
    return dec if isinstance(dec, ast.Call) else True


def _static_skip(call: ast.Call | bool) -> bool:
    if not isinstance(call, ast.Call):
        return False
    return any(
        kw.arg == "skip" and isinstance(kw.value, ast.Constant) and bool(kw.value.value)
        for kw in call.keywords
    )


def _expects_failure(call: ast.Call | bool) -> bool:
    if not isinstance(call, ast.Call):
        return False
    for kw in call.keywords:
        if kw.arg == "expect_error":
            return True
        if kw.arg == "expect_fail" and not (
            isinstance(kw.value, ast.Constant) and not kw.value.value
        ):
            return True
    return False


def _parse_covers_lines(doc: str, where: str, warnings: list[str]) -> tuple[list[str], bool]:
    """Return the ids named and whether the docstring has any `Covers:` or look-alike line."""
    ids: dict[str, None] = {}
    saw_line = False
    for raw in doc.splitlines():
        match = _COVERS_LINE.match(raw)
        if match is None:
            if _COVERS_ANY_CASE.match(raw):
                saw_line = True
                warnings.append(
                    f"{where}: malformed Covers line {raw.strip()!r}; write it exactly as "
                    "'Covers: R1, R2' (capital C, colon)"
                )
            continue
        saw_line = True
        tokens = [t for t in re.split(r"[,\s]+", match.group(1).strip()) if t]
        if not tokens:
            warnings.append(f"{where}: 'Covers:' line names no requirement; write 'Covers: R1'")
        for token in tokens:
            if REQ_ID_RE.fullmatch(token):
                ids[token] = None
            else:
                warnings.append(
                    f"{where}: malformed requirement id {token!r} in a Covers line; "
                    "ids look like R1, R2, R10"
                )
    return list(ids), saw_line


def parse_covers(
    source_text: str, filename: str, spec_ids: Iterable[str] | None = None
) -> CoversReport:
    """Find the cocotb tests in `source_text` and the requirement ids each one covers.

    With `spec_ids`, an id that is not in the spec is a warning. A file that does not parse gives
    a report with `error` set and no tests, never an exception.
    """
    try:
        tree = ast.parse(source_text, filename=filename)
    except SyntaxError as exc:
        error = {"file": filename, "line": exc.lineno or 0, "col": exc.offset or 0, "msg": exc.msg}
        return CoversReport(error=error)
    except (ValueError, RecursionError, MemoryError) as exc:
        message = f"could not be parsed ({type(exc).__name__})"
        return CoversReport(error={"file": filename, "line": 0, "col": 0, "msg": message})
    known = None if spec_ids is None else set(spec_ids)
    modules, test_names = _cocotb_names(tree)
    tests: list[CocotbTest] = []
    warnings: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        decorator: ast.Call | bool = False
        for dec in node.decorator_list:
            decorator = _is_test_decorator(dec, modules, test_names)
            if decorator:
                break
        if not decorator:
            continue
        where = f"{filename}:{node.lineno}: test {node.name}"
        covers, saw_line = _parse_covers_lines(ast.get_docstring(node) or "", where, warnings)
        skipped = _static_skip(decorator)
        if not saw_line:
            warnings.append(f"{where}: no 'Covers:' line in the docstring, so it covers nothing")
        if skipped:
            warnings.append(f"{where}: skip=True, so it does not count as coverage")
        expects_failure = _expects_failure(decorator)
        if expects_failure:
            warnings.append(
                f"{where}: expect_fail/expect_error makes a failing test count as PASSED, so it "
                "does not count as coverage"
            )
        if any(t.name == node.name for t in tests):
            warnings.append(f"{where}: duplicate test name; only the last definition runs")
        if known is not None:
            for rid in covers:
                if rid not in known:
                    warnings.append(f"{where}: unknown requirement {rid} (not in spec.yaml)")
        tests.append(CocotbTest(node.name, tuple(covers), skipped, node.lineno, expects_failure))
    return CoversReport(tests=tests, warnings=warnings)


@dataclass(frozen=True)
class CoverageResult:
    covered: list[str]
    uncovered: list[str]
    unknown: list[str]

    def as_dict(self) -> dict[str, list[str]]:
        return {"covered": self.covered, "uncovered": self.uncovered, "unknown": self.unknown}


def coverage(
    requirement_ids: Iterable[str], test_results: Iterable[Mapping[str, Any]]
) -> CoverageResult:
    """Which requirements are covered by passing tests.

    `test_results` are `{name, covers, outcome}`. Only `outcome == "pass"` counts: skipped, failed
    and errored tests do not cover anything. A result with `expects_failure` true (from
    `parse_covers`: expect_fail/expect_error tests report a failing check as a pass) does not
    count either, so callers must pass that flag through. `unknown` lists ids that tests name
    but the spec does not have (whatever the outcome), sorted by number.
    """
    required = list(dict.fromkeys(requirement_ids))
    passing: set[str] = set()
    named: set[str] = set()
    for result in test_results:
        covers = [str(c) for c in result.get("covers", [])]
        named.update(covers)
        if result.get("outcome") == "pass" and not result.get("expects_failure"):
            passing.update(covers)
    unknown = sorted(named - set(required), key=_id_sort_key)
    return CoverageResult(
        covered=[r for r in required if r in passing],
        uncovered=[r for r in required if r not in passing],
        unknown=unknown,
    )


def _id_sort_key(rid: str) -> tuple[int, int, str]:
    if REQ_ID_RE.fullmatch(rid):
        return (0, int(rid[1:]), rid)
    return (1, 0, rid)
