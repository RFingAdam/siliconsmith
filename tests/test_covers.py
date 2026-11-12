# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import sys
from pathlib import Path

from siliconsmith.covers import coverage, parse_covers

FIXTURE = Path(__file__).parent / "fixtures" / "covers" / "test_never_import.py"


def fixture_report(spec_ids: list[str] | None = None):  # type: ignore[no-untyped-def]
    return parse_covers(FIXTURE.read_text(), "test/test.py", spec_ids)


def by_name(report):  # type: ignore[no-untyped-def]
    return {t.name: t for t in report.tests}


def test_tags_are_parsed_without_importing_the_file() -> None:
    before = set(sys.modules)
    report = fixture_report()
    assert report.error is None
    tests = by_name(report)
    assert list(tests) == [
        "test_covered",
        "test_multi",
        "test_unknown",
        "test_no_covers",
        "test_skipped",
        "test_bad_tags",
    ]
    assert tests["test_covered"].covers == ("R1",)
    assert tests["test_multi"].covers == ("R2", "R3", "R10")  # comma or space separated
    assert tests["test_unknown"].covers == ("R1", "R99")
    assert tests["test_no_covers"].covers == ()
    assert tests["test_skipped"].covers == ("R4",)
    assert tests["test_covered"].line == 14
    assert "helper_not_a_test" not in tests
    # The file was never imported: no module from it appeared, and importing it would raise.
    assert "test_never_import" not in sys.modules
    assert not any(getattr(m, "__file__", None) == str(FIXTURE) for m in sys.modules.values())
    assert not [n for n in set(sys.modules) - before if "never_import" in n]


def test_static_skip_is_recorded() -> None:
    tests = by_name(fixture_report())
    assert tests["test_skipped"].skipped_static
    assert not any(t.skipped_static for n, t in tests.items() if n != "test_skipped")
    assert "R4" not in fixture_report().covered_ids()
    assert any("skip=True" in w for w in fixture_report().warnings)


def test_unknown_requirement_id_is_a_warning() -> None:
    report = fixture_report(["R1", "R2", "R3", "R4", "R10"])
    unknown = [w for w in report.warnings if "unknown requirement" in w]
    assert len(unknown) == 1 and "R99" in unknown[0] and "test_unknown" in unknown[0]
    assert report.error is None  # a warning, not an error
    assert not [w for w in fixture_report().warnings if "unknown requirement" in w]


def test_missing_and_malformed_covers_lines_warn() -> None:
    warnings = fixture_report().warnings
    assert any("test_no_covers" in w and "no 'Covers:' line" in w for w in warnings)
    assert any("test_bad_tags" in w and "malformed Covers line" in w for w in warnings)
    assert any("test_bad_tags" in w and "'banana'" in w for w in warnings)
    assert by_name(fixture_report())["test_bad_tags"].covers == ("R1",)


def test_decorator_forms() -> None:
    source = (
        "import cocotb as ct\n"
        "from cocotb import test as t\n"
        "@ct.test\nasync def a(dut):\n    '''Covers: R1'''\n"
        "@ct.test()\nasync def b(dut):\n    '''Covers: R2'''\n"
        "@t\nasync def c(dut):\n    '''Covers: R3'''\n"
        "@t(skip=False)\nasync def d(dut):\n    '''Covers: R4'''\n"
        "@other.test\nasync def e(dut):\n    '''Covers: R5'''\n"
    )
    report = parse_covers(source, "x.py")
    assert [(t.name, t.covers, t.skipped_static) for t in report.tests] == [
        ("a", ("R1",), False),
        ("b", ("R2",), False),
        ("c", ("R3",), False),
        ("d", ("R4",), False),
    ]


def test_keyword_is_case_sensitive_and_empty_line_warns() -> None:
    source = (
        "import cocotb\n@cocotb.test()\nasync def a(dut):\n"
        "    '''COVERS: R1\n    Covers:\n    '''\n"
    )
    report = parse_covers(source, "x.py")
    assert report.tests[0].covers == ()
    assert sum("malformed Covers line" in w for w in report.warnings) == 1
    assert any("names no requirement" in w for w in report.warnings)


def test_syntax_error_is_a_structured_error() -> None:
    report = parse_covers("x = 1\nasync def (:\n", "test/test.py")
    assert report.tests == [] and report.error is not None
    assert report.error["file"] == "test/test.py" and report.error["line"] == 2
    assert parse_covers("a\0b", "t.py").error is not None


def test_coverage_counts_only_passing_tests() -> None:
    results = [
        {"name": "a", "covers": ["R1"], "outcome": "pass"},
        {"name": "b", "covers": ["R2"], "outcome": "skipped"},
        {"name": "c", "covers": ["R3"], "outcome": "fail"},
        {"name": "d", "covers": ["R4"], "outcome": "error"},
        {"name": "e", "covers": ["R5", "R99"], "outcome": "pass"},
        {"name": "f", "covers": ["R2"], "outcome": "fail"},
    ]
    result = coverage(["R1", "R2", "R3", "R4", "R5", "R6"], results)
    assert result.covered == ["R1", "R5"]
    assert result.uncovered == ["R2", "R3", "R4", "R6"]
    assert result.unknown == ["R99"]
    assert result.as_dict()["covered"] == ["R1", "R5"]


def test_coverage_a_pass_beside_a_skip_still_covers() -> None:
    results = [
        {"name": "a", "covers": ["R1"], "outcome": "skipped"},
        {"name": "b", "covers": ["R1"], "outcome": "pass"},
    ]
    assert coverage(["R1"], results).covered == ["R1"]
    assert coverage([], []).covered == []


def test_expect_fail_and_error_never_count_as_coverage() -> None:
    src = (
        "import cocotb\n"
        "@cocotb.test(expect_fail=True)\nasync def a(dut):\n    '''Covers: R1'''\n"
        "@cocotb.test(expect_error=ValueError)\nasync def b(dut):\n    '''Covers: R2'''\n"
        "@cocotb.test(expect_fail=False)\nasync def c(dut):\n    '''Covers: R3'''\n"
    )
    report = parse_covers(src, "t.py")
    flags = {t.name: t.expects_failure for t in report.tests}
    assert flags == {"a": True, "b": True, "c": False}
    assert report.covered_ids() == ["R3"]
    assert sum("does not count as coverage" in w for w in report.warnings) == 2
    results = [dict(t.as_dict(), outcome="pass") for t in report.tests]
    assert coverage(["R1", "R2", "R3"], results).covered == ["R3"]


def test_duplicate_names_warn_and_truthy_skip_is_static() -> None:
    src = (
        "import cocotb\n"
        "@cocotb.test()\nasync def a(dut):\n    '''Covers: R1'''\n"
        "@cocotb.test(skip=1)\nasync def a(dut):\n    '''Covers: R2'''\n"
    )
    report = parse_covers(src, "t.py")
    assert any("duplicate test name" in w for w in report.warnings)
    assert [t.skipped_static for t in report.tests] == [False, True]
