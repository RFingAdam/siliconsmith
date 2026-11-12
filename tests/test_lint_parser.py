# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The Verilator output parser, on real output captured from Verilator 5.046 in the image."""

from __future__ import annotations

from pathlib import Path

from conftest import REPO_ROOT

from siliconsmith.worker.lint import parse

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "verilator"


def parsed(name: str) -> list[dict[str, object]]:
    return list(parse((FIXTURES / name).read_text()))


def brief(findings: list[dict[str, object]]) -> list[tuple[object, ...]]:
    return [(f["severity"], f["code"], f["file"], f["line"], f["col"]) for f in findings]


def test_width_warnings() -> None:
    findings = parsed("widthtrunc_widthexpand.txt")
    assert brief(findings) == [
        ("warning", "WIDTHTRUNC", "t_width.v", 3, 12),
        ("warning", "WIDTHEXPAND", "t_width.v", 4, 12),
        ("warning", "UNUSEDSIGNAL", "t_width.v", 2, 28),
    ]
    assert findings[0]["msg"] == (
        "Operator ASSIGNW expects 4 bits on the Assign RHS, "
        "but Assign RHS's VARREF 'a' generates 8 bits."
    )
    assert "expects 16 bits" in str(findings[1]["msg"])
    assert findings[2]["msg"] == "Bits of signal are not used: 'a'[7:4]"


def test_continuation_lines_attach_to_the_previous_finding() -> None:
    first, second, _ = parsed("widthtrunc_widthexpand.txt")
    assert first["detail"] == "... note: In instance 'w'"
    assert second["detail"] == "... note: In instance 'w'"
    # The source excerpt and the manual links are not kept.
    assert "assign" not in str(first["detail"]) and "verilator.org" not in str(first["detail"])


def test_latch_is_an_error() -> None:
    (finding,) = parsed("latch.txt")
    assert finding["severity"] == "error" and finding["code"] == "LATCH"
    assert (finding["line"], finding["col"]) == (3, 3)
    assert "Latch inferred for signal 'y'" in str(finding["msg"])
    assert "always_latch" in str(finding["detail"])


def test_multidriven_with_locations_in_the_notes() -> None:
    (finding,) = parsed("multidriven.txt")
    assert finding["code"] == "MULTIDRIVEN" and finding["severity"] == "error"
    assert finding["msg"] == "Signal has multiple driving blocks with different clocking: 'y'"
    assert "t_multi.v:3:25: ... Location of first driving block" in str(finding["detail"])
    assert "t_multi.v:4:25: ... Location of other driving block" in str(finding["detail"])


def test_unused_signal() -> None:
    findings = parsed("unusedsignal.txt")
    assert brief(findings) == [
        ("warning", "UNUSEDSIGNAL", "t_unused.v", 2, 28),
        ("warning", "UNUSEDSIGNAL", "t_unused.v", 2, 42),
    ]
    assert findings[1]["msg"] == "Signal is not used: 'b'"


def test_syntax_error_has_no_code_in_verilator_so_it_gets_one() -> None:
    (finding,) = parsed("syntax_error.txt")
    assert brief([finding]) == [("error", "SYNTAX", "t_syn.v", 2, 3)]
    assert finding["msg"] == "syntax error, unexpected assign, expecting ';'"


def test_error_without_a_position() -> None:
    (finding,) = parsed("missing_file.txt")
    assert brief([finding]) == [("error", "ERROR", "", 0, 0)]
    assert "Cannot find file containing module" in str(finding["msg"])


def test_clean_run_and_banner_have_no_findings() -> None:
    # The `- V e r i l a t i o n   R e p o r t` banner is printed when nothing is wrong.
    assert parsed("clean_run.txt") == []
    assert parse("") == []


def test_exiting_line_is_not_a_finding_and_does_not_take_continuations() -> None:
    text = (
        "%Warning-WIDTHTRUNC: a.v:1:2: msg\n"
        "    1 | x\n"
        "%Error: Exiting due to 1 warning(s)\n"
        "        ... stray indented line\n"
    )
    (finding,) = parse(text)
    assert finding["detail"] == ""


def test_many_findings_keep_their_order() -> None:
    text = "".join(f"%Warning-WIDTHTRUNC: a.v:{n}:1: m{n}\n" for n in range(1, 6))
    assert [f["line"] for f in parse(text)] == [1, 2, 3, 4, 5]


def test_fixture_folder_is_complete() -> None:
    assert sorted(p.name for p in Path(FIXTURES).glob("*.txt")) == [
        "clean_run.txt",
        "latch.txt",
        "missing_file.txt",
        "multidriven.txt",
        "syntax_error.txt",
        "unusedsignal.txt",
        "widthtrunc_widthexpand.txt",
    ]
