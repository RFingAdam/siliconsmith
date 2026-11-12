# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import yaml

from siliconsmith.result import make_summary, summary_within_limits
from siliconsmith.spec import (
    MAX_SPEC_BYTES,
    Spec,
    SpecError,
    parse_spec,
    pinout,
    render_spec,
    stub_spec,
)
from siliconsmith.tools.write_spec import _summary_lines

VALID = """\
# a comment
title: Counter
summary: Counts up while enable is high.
clock_hz: 10000000
reset: "rst_n, active-low, synchronous"
pins:
  ui:
    0: enable
  uo:
    7: top bit
requirements:
  - id: R1
    text: Counts up by one each clock while enable is high.
  - id: R2
    text: Output voltage stays within range.
    limit: {min: 0.5, typ: 1, max: 1.5, unit: V}
open_questions:
  - Is 10 MHz right?
"""


def errors_of(text: str) -> list[SpecError]:
    result = parse_spec(text)
    assert isinstance(result, list), "expected the spec to be rejected"
    # Whatever the errors, the tool summary stays within the limits.
    assert summary_within_limits(make_summary(_summary_lines(result)))
    return result


def paths(text: str) -> list[str]:
    return [e.path for e in errors_of(text)]


def test_valid_spec_parses() -> None:
    spec = parse_spec(VALID)
    assert isinstance(spec, Spec)
    assert spec.version == 1 and spec.clock_hz == 10_000_000
    assert spec.requirement_ids() == ["R1", "R2"]
    assert spec.pins.ui == {0: "enable"} and spec.pins.uio == {}
    assert spec.open_questions == ["Is 10 MHz right?"]


def test_missing_clock_hz_names_the_field() -> None:
    errors = errors_of(VALID.replace("clock_hz: 10000000\n", ""))
    assert errors == [SpecError("clock_hz", "required field is missing")]


def test_bad_clock_hz_values() -> None:
    for bad in ("0", "-5", "true", "1.5", '"fast"'):
        assert paths(VALID.replace("10000000", bad)) == ["clock_hz"], bad


def test_bad_requirement_ids_name_the_index() -> None:
    for bad in ("r1", "R0", "R01", "R", "1", "R1a", "REQ1"):
        text = VALID.replace("id: R2", f"id: {bad}")
        assert paths(text) == ["requirements[1].id"], bad
    text = VALID.replace("id: R2", "id: R1")
    errors = errors_of(text)
    assert [e.path for e in errors] == ["requirements[1].id"]
    assert "duplicate" in errors[0].message and "requirements[0]" in errors[0].message


def test_out_of_range_pin_index_names_the_pin() -> None:
    assert paths(VALID.replace("    0: enable", "    8: enable")) == ["pins.ui.8"]
    assert paths(VALID.replace("    7: top bit", "    -1: top bit")) == ["pins.uo.-1"]
    assert paths(VALID.replace("    0: enable", "    x: enable")) == ["pins.ui.x"]
    assert "0 to 7" in errors_of(VALID.replace("    0: enable", "    9: enable"))[0].message


def test_empty_or_multiline_pin_meaning_is_rejected() -> None:
    assert paths(VALID.replace("0: enable", '0: ""')) == ["pins.ui.0"]
    assert paths(VALID.replace("0: enable", '0: "a\\nb"')) == ["pins.ui.0"]


def test_unknown_keys_are_rejected_at_every_level() -> None:
    top = errors_of(VALID + "colour: red\n")
    assert [e.path for e in top] == ["colour"] and "unknown key" in top[0].message
    assert "clock_hz" in top[0].message  # lists what is allowed
    assert paths(VALID.replace("  uo:\n", "  uox:\n")) == ["pins.uox"]
    assert paths(VALID.replace("    text: Counts", "    note: x\n    text: Counts")) == [
        "requirements[0].note"
    ]
    assert paths(VALID.replace("unit: V", "unit: V, vmax: 2")) != []
    assert paths(VALID.replace("max: 1.5", "max: 1.5, mean: 1")) == ["requirements[1].limit.mean"]


def test_several_errors_are_all_reported_in_few_lines() -> None:
    text = (
        "title: T\nsummary: S\nreset: r\n"
        "pins:\n  ui:\n    9: a\n    10: b\n"
        "requirements:\n  - {id: x, text: t}\n  - {id: y, text: t}\n"
        "extra: 1\n"
    )
    errors = errors_of(text)
    assert {e.path for e in errors} == {
        "clock_hz",
        "pins.ui.9",
        "pins.ui.10",
        "requirements[0].id",
        "requirements[1].id",
        "extra",
    }


def test_many_errors_still_fit_in_twenty_lines() -> None:
    reqs = "".join(f"  - {{id: bad{i}, text: t}}\n" for i in range(60))
    errors = errors_of(f"title: T\nsummary: S\nclock_hz: 1\nreset: r\nrequirements:\n{reqs}")
    assert len(errors) >= 50
    summary = make_summary(_summary_lines(errors))
    assert len(summary.splitlines()) <= 20 and "more lines in details" in summary


def test_required_fields_and_types() -> None:
    assert paths("title: T\n") == ["summary", "clock_hz", "reset", "requirements"]
    assert paths(VALID.replace("title: Counter", "title: 5")) == ["title"]
    assert errors_of("[1, 2]") == [
        SpecError("(file)", "must be a YAML mapping with keys such as title, clock_hz")
    ]
    assert paths("") == ["(file)"]
    assert paths("version: 2\n" + VALID) == ["version"]


def test_text_length_and_line_rules() -> None:
    assert paths(VALID.replace("title: Counter", f"title: {'x' * 101}")) == ["title"]
    assert paths(VALID.replace("title: Counter", 'title: "a\\nb"')) == ["title"]
    ok = VALID.replace("summary: Counts up while enable is high.", 'summary: "two\\nlines"')
    assert isinstance(parse_spec(ok), Spec)


def test_limit_round_trips() -> None:
    spec = parse_spec(VALID)
    assert isinstance(spec, Spec)
    limit = spec.requirements[1].limit
    assert limit is not None
    assert (limit.min, limit.typ, limit.max, limit.unit) == (0.5, 1, 1.5, "V")
    data = spec.to_dict()
    assert data["requirements"][1]["limit"] == {"min": 0.5, "typ": 1, "max": 1.5, "unit": "V"}
    assert "limit" not in data["requirements"][0]
    assert data["pins"]["ui"] == {0: "enable"}
    again = parse_spec(yaml.safe_dump(data, sort_keys=False))
    assert isinstance(again, Spec) and again == spec
    # A partial limit stays partial.
    only_max = parse_spec(VALID.replace("{min: 0.5, typ: 1, max: 1.5, unit: V}", "{max: 3}"))
    assert isinstance(only_max, Spec)
    assert only_max.to_dict()["requirements"][1]["limit"] == {"max": 3}


def test_limit_rules() -> None:
    def limit(body: str) -> list[SpecError]:
        return errors_of(VALID.replace("{min: 0.5, typ: 1, max: 1.5, unit: V}", body))

    assert limit("{}")[0].path == "requirements[1].limit"
    assert limit("{unit: V}")[0].path == "requirements[1].limit"
    assert "min must be <= typ" in limit("{min: 2, typ: 1}")[0].message
    assert "typ must be <= max" in limit("{typ: 3, max: 1}")[0].message
    assert "min must be <= max" in limit("{min: 3, max: 1}")[0].message
    assert limit("{min: .inf}")[0].path == "requirements[1].limit.min"
    assert limit("{min: true}")[0].path == "requirements[1].limit.min"
    assert limit('{min: "1"}')[0].path == "requirements[1].limit.min"
    assert limit("{min: 1, unit: 5}")[0].path == "requirements[1].limit.unit"


def test_yaml_hazards_are_rejected() -> None:
    alias = VALID.replace("text: Counts up by one each clock while enable is high.", "text: &a hi")
    alias += "x: *a\n"
    errors = errors_of(alias)
    assert errors[0].path == "yaml" and "anchors" in errors[0].message
    only_alias = errors_of("title: T\nsummary: *a\n")
    assert only_alias[0].path == "yaml" and "aliases" in only_alias[0].message
    bomb = "a: &a [x, x]\nb: &b [*a, *a]\n"
    assert errors_of(bomb)[0].path == "yaml"
    dup = errors_of(VALID + "title: Again\n")
    assert dup[0].path == "yaml" and "duplicate key 'title'" in dup[0].message
    assert "line 2" in errors_of("a: 1\na: 2\n")[0].message.replace("line 2,", "line 2")
    assert "merge" in errors_of("title: T\n<<: {a: 1}\n")[0].message


def test_yaml_syntax_error_has_line_and_column() -> None:
    errors = errors_of("title: T\nsummary: [unclosed\nclock_hz: 1\n")
    assert len(errors) == 1 and errors[0].path == "yaml"
    assert errors[0].message.startswith("line ") and "column" in errors[0].message


def test_python_object_tags_are_not_constructed() -> None:
    errors = errors_of("title: !!python/object/apply:os.system ['echo hi']\n")
    assert errors[0].path == "yaml"


def test_oversize_and_deep_nesting_are_rejected() -> None:
    big = "title: T\n# " + "x" * MAX_SPEC_BYTES + "\n"
    errors = errors_of(big)
    assert errors == [
        SpecError("(file)", f"is {len(big)} bytes; the limit is {MAX_SPEC_BYTES} bytes")
    ]
    assert errors_of("a: " + "[" * 5000 + "]" * 5000)[0].path == "yaml"
    assert parse_spec("a\udc80") == [SpecError("(file)", "text is not valid UTF-8")]


def test_stub_spec_is_valid_and_deterministic() -> None:
    spec = stub_spec("My chip", "Adds two numbers.")
    text = render_spec(spec)
    assert text == render_spec(stub_spec("My chip", "Adds two numbers."))
    parsed = parse_spec(text)
    assert isinstance(parsed, Spec) and parsed == spec
    assert spec.clock_hz == 10_000_000 and spec.requirement_ids() == ["R1"]
    assert spec.open_questions and "clock" in spec.open_questions[0]
    assert text.startswith("# spec.yaml")


def test_stub_round_trips_awkward_text() -> None:
    for title in ('He said "hi"', "colon: here", "# not a comment", "ünï ✓", "a\\b", "  x  "):
        spec = stub_spec(title, title)
        again = parse_spec(render_spec(spec))
        assert isinstance(again, Spec) and again.title == title


def test_pinout_lists_all_24_pins() -> None:
    spec = parse_spec(VALID)
    assert isinstance(spec, Spec)
    out = pinout(spec)
    assert len(out) == 24 and list(out)[:2] == ["ui[0]", "ui[1]"]
    assert out["ui[0]"] == "enable" and out["uo[7]"] == "top bit" and out["uio[3]"] == ""
