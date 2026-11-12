# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""results.xml and failure-message parsing. The XML fixtures were written by cocotb 2.1.0 in the
toolchain image (`seeded_bug.xml`: the counter with a seeded wrap bug; `messages.xml`: tests with
and without their own assertion message); only the host names were edited."""

from __future__ import annotations

from pathlib import Path

import pytest

from siliconsmith.worker import results
from siliconsmith.worker.limits import MAX_TESTS, TEST_TEXT_MAX

FIXTURES = Path(__file__).parent / "fixtures" / "results"


def by_name(records: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {str(r["name"]): r for r in records}


def test_seeded_bug_fixture() -> None:
    records = by_name(results.parse_junit((FIXTURES / "seeded_bug.xml").read_bytes()))
    assert {n: r["outcome"] for n, r in records.items()} == {
        "test_reset_clears_count": "pass",
        "test_counts_when_enabled": "pass",
        "test_holds_when_disabled": "pass",
        "test_wraps_at_255": "fail",
    }
    bad = records["test_wraps_at_255"]
    assert bad["module"] == "test"
    assert (bad["file"], bad["line"]) == ("test/test.py", 65)
    assert bad["assertion"] == "assert await run_enabled(dut, 255) == 255"
    assert (bad["expected"], bad["actual"]) == ("255", "0")
    assert bad["message"] is None  # a bare assert: the assertion says everything
    assert records["test_wraps_at_255"]["sim_time_ns"] == pytest.approx(2615.0, abs=30)
    assert records["test_reset_clears_count"]["sim_time_ns"] == 55.0


def test_explicit_message_wins_and_logic_array_becomes_a_number() -> None:
    records = by_name(results.parse_junit((FIXTURES / "messages.xml").read_bytes()))
    with_message = records["test_counts_with_message"]
    assert (with_message["file"], with_message["line"]) == ("test/test_counter.py", 21)
    assert with_message["expected"] == "10" and with_message["actual"] == "0"
    assert str(with_message["message"]).startswith("AssertionError: uo_out after 10 enabled")
    bare = records["test_counts_bare_assert"]
    assert bare["assertion"] == "assert int(dut.uo_out.value) == expected"
    assert (bare["expected"], bare["actual"]) == ("10", "0")
    assert records["test_hold_when_disabled"]["outcome"] == "pass"


TRACE = """\
Traceback (most recent call last):
  File "/venv/lib/python3.12/site-packages/cocotb/_decorators.py", line 1, in run
    something()
  File "/work/test/test.py", line 30, in test_x
    assert dut.uo_out.value == 10
AssertionError: assert LogicArray('00001010', Range(7, 'downto', 0)) == 10
 +  where LogicArray('00001010', Range(7, 'downto', 0)) = PackedObject(tb.uo_out).value
"""


def frame(source: str, last: str) -> str:
    return f'File "/work/test/test.py", line 3, in t\n    {source}\n{last}\n'


@pytest.mark.parametrize(
    ("text", "expected", "actual"),
    [
        (TRACE.replace("00001010", "11111111"), "10", "255"),
        (TRACE.replace("00001010", "0000x010"), "10", "0000x010"),
        (frame("assert a == b", "AssertionError: expected 5, got 7"), "5", "7"),
        (frame("assert a == b", "AssertionError: got 7 but expected 5"), "5", "7"),
        (
            frame("assert f(a, b) == (1, 2)", "AssertionError: assert (3, 4) == (1, 2)"),
            "(1, 2)",
            "(3, 4)",
        ),
        (
            frame(
                "assert x == 255, 'msg'",
                "AssertionError: Expected the counter to wrap but it was still counting\n"
                "assert 254 == 255",
            ),
            "255",
            "254",
        ),
        (
            frame("assert a == b", "AssertionError: expected (1, 2), got (3, 4)"),
            "(1, 2)",
            "(3, 4)",
        ),
        (frame("assert a == b", "AssertionError: Expected the counter to wrap but it was"), "", ""),
        (
            frame("assert a == b", "AssertionError: assert 'a == b' == 'c'"),
            "'c'",
            "'a == b'",
        ),
    ],
)
def test_expected_and_actual(text: str, expected: str, actual: str) -> None:
    found = results.analyze_failure(text)
    assert (found["expected"] or "", found["actual"] or "") == (expected, actual)
    assert found["file"] == "test/test.py"


def test_deepest_test_frame_and_non_assertion_errors() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "/work/test/test.py", line 40, in test_a\n    await check(dut)\n'
        '  File "/work/test/helpers.py", line 7, in check\n    assert dut.x.value != 3\n'
        "AssertionError: assert 3 != 3\n"
    )
    found = results.analyze_failure(text)
    assert (found["file"], found["line"]) == ("test/helpers.py", 7)
    assert found["assertion"] == "assert dut.x.value != 3"
    assert found["expected"] is None and found["actual"] is None  # only == has an expected side
    error = results.analyze_failure(
        'File "/work/test/test.py", line 9, in t\n    dut.nothing.value = 1\n'
        "AttributeError: tb contains no object named nothing\n"
    )
    assert error["message"] == "AttributeError: tb contains no object named nothing"
    assert error["expected"] is None and error["line"] == 9
    assert results.analyze_failure("") == {
        "file": None, "line": None, "assertion": None, "message": None,
        "expected": None, "actual": None,
    }  # fmt: skip


def test_long_text_is_clipped() -> None:
    text = (
        'File "/work/test/test.py", line 3, in t\n    assert x\n'
        "AssertionError: " + "y" * 5000 + "\n"
    )
    assert len(str(results.analyze_failure(text)["message"])) <= TEST_TEXT_MAX


def xml(body: str) -> bytes:
    return f"<testsuites><testsuite>{body}</testsuite></testsuites>".encode()


def test_outcomes_and_skips() -> None:
    records = results.parse_junit(
        xml(
            '<testcase classname="t" name="a"><skipped message="s"/></testcase>'
            '<testcase classname="t" name="b"><error message="boom">E: boom</error></testcase>'
            '<testcase classname="t" name="c"/>'
        )
    )
    assert [r["outcome"] for r in records] == ["skip", "error", "pass"]
    assert records[1]["message"] == "boom"
    assert [r["sim_time_ns"] for r in records] == [None, None, None]


def test_time_units() -> None:
    props = (
        '<properties><property name="sim_time_unit" value="us"/>'
        '<property name="sim_time_duration" value="1.5"/></properties>'
    )
    (record,) = results.parse_junit(xml(f'<testcase classname="t" name="a">{props}</testcase>'))
    assert record["sim_time_ns"] == 1500.0


@pytest.mark.parametrize(
    "data",
    [
        b"not xml",
        b'<!DOCTYPE x [<!ENTITY a "b">]><testsuites/>',
        b"<!doctype html><testsuites/>",
        b'<?xml version="1.0"?><!ENTITY x "y"><testsuites/>',
        "<testsuites>é</testsuites>".encode("utf-16"),
        b"\xff\xfe<testsuites/>",
        b"<testsuites>" + b"a" * (results.MAX_XML_BYTES + 1),
    ],
)
def test_untrusted_xml_is_rejected(data: bytes) -> None:
    with pytest.raises(results.ResultsError):
        results.parse_junit(data)


def test_test_count_is_capped() -> None:
    cases = "".join(f'<testcase classname="t" name="n{i}"/>' for i in range(MAX_TESTS + 20))
    assert len(results.parse_junit(xml(cases))) == MAX_TESTS
