# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Under section 7 of the GNU AGPL version 3, you are granted the additional
# permission in OUTPUT-EXCEPTION.md: files this module writes into a user's
# project, and the text it copies into them, may be used under Apache-2.0.

"""The `spec.yaml` model, version 1 (PLAN.md section 5.7).

`spec.yaml` is the single source of the clock, the pins and the requirements. It is short, so the
owner can read it and judge it. `parse_spec` never raises for bad input: it returns a `Spec`, or a
list of `SpecError` (a field path and a message each), so a tool can show the agent exactly what
to fix. The YAML is read with a safe loader that also refuses anchors, aliases and duplicate keys.
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import yaml
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from siliconsmith.paths import safe_read
from siliconsmith.tiles import DEFAULT_TILES, allowed_tiles, unknown_tiles_message

SPEC_NAME = "spec.yaml"
SPEC_VERSION = 1
MAX_SPEC_BYTES = 64 * 1024
MAX_ERRORS = 50
MAX_CLOCK_HZ = 1_000_000_000
MAX_MAGNITUDE = 1e300
REQ_ID_RE = re.compile(r"^R[1-9][0-9]*$")
PIN_GROUPS = ("ui", "uo", "uio")

_TOP_KEYS = (
    "version",
    "title",
    "summary",
    "clock_hz",
    "tiles",
    "reset",
    "pins",
    "requirements",
    "open_questions",
)
_ALLOWED_KEYS: dict[str, tuple[str, ...]] = {
    "": _TOP_KEYS,
    "pins": PIN_GROUPS,
    "requirements[]": ("id", "text", "limit"),
    "requirements[].limit": ("min", "typ", "max", "unit"),
}


# --- text fields -----------------------------------------------------------------------------


_LINE_BREAKS = "\u2028\u2029"


def _has_control(text: str, *, allow_newline: bool) -> bool:
    for ch in text:
        if ch == "\n" and allow_newline:
            continue
        if ord(ch) < 32 or ord(ch) == 127 or 0x80 <= ord(ch) < 0xA0 or ch in _LINE_BREAKS:
            return True
    return False


def _check_text(value: str, *, max_len: int, multiline: bool) -> str:
    if not value.strip():
        raise ValueError("must not be empty")
    if len(value) > max_len:
        raise ValueError(f"is {len(value)} characters; the limit is {max_len}")
    if _has_control(value, allow_newline=multiline):
        raise ValueError(
            "must not contain control characters"
            + ("" if multiline else " or line breaks (use a single line)")
        )
    return value


def _checker(max_len: int, *, multiline: bool) -> AfterValidator:
    return AfterValidator(lambda v: _check_text(v, max_len=max_len, multiline=multiline))


Line20 = Annotated[StrictStr, _checker(20, multiline=False)]
Line100 = Annotated[StrictStr, _checker(100, multiline=False)]
Line200 = Annotated[StrictStr, _checker(200, multiline=False)]
Block500 = Annotated[StrictStr, _checker(500, multiline=True)]
Block1000 = Annotated[StrictStr, _checker(1000, multiline=True)]


def _check_pin_bit(bit: int) -> int:
    if not 0 <= bit <= 7:
        raise ValueError("pin bit must be an integer from 0 to 7")
    return bit


PinBit = Annotated[StrictInt, AfterValidator(_check_pin_bit)]
PinMap = dict[PinBit, Line200]


def _check_number(value: Any) -> Any:
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            pass
        else:
            raise ValueError(
                f"must be a number; {value!r} was read as text. YAML needs a dot and a signed "
                "exponent, e.g. 1.0e-6 or 2.0e+9"
            )
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a number")
    try:
        magnitude = abs(float(value))
    except OverflowError:
        raise ValueError(f"is too large; the limit is {MAX_MAGNITUDE:g}") from None
    if not math.isfinite(magnitude):
        raise ValueError("must be a finite number")
    if magnitude > MAX_MAGNITUDE:
        raise ValueError(f"is too large; the limit is {MAX_MAGNITUDE:g}")
    return value


# An int stays an int and a float stays a float, so a limit round-trips unchanged.
Number = Annotated[Any, AfterValidator(_check_number)]


# --- models ----------------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pins(_Strict):
    ui: PinMap = Field(default_factory=dict)
    uo: PinMap = Field(default_factory=dict)
    uio: PinMap = Field(default_factory=dict)


class Limit(_Strict):
    min: Number | None = None
    typ: Number | None = None
    max: Number | None = None
    unit: Line20 | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Limit:
        present: list[tuple[str, float]] = []
        for name, value in (("min", self.min), ("typ", self.typ), ("max", self.max)):
            if value is not None:
                present.append((name, value))
        if not present:
            raise ValueError("a limit needs at least one of min, typ, max")
        for (low_name, low), (high_name, high) in itertools.pairwise(present):
            if low > high:
                raise ValueError(f"{low_name} must be <= {high_name} ({low} > {high})")
        return self


class Requirement(_Strict):
    id: StrictStr
    text: Block1000
    limit: Limit | None = None

    @field_validator("id")
    @classmethod
    def _id_format(cls, value: str) -> str:
        if not REQ_ID_RE.fullmatch(value):
            raise ValueError("requirement id must look like R1, R2, R10 (capital R, then a number)")
        return value


class Spec(_Strict):
    version: StrictInt = SPEC_VERSION
    title: Line100
    summary: Block1000
    clock_hz: StrictInt = Field(gt=0, le=MAX_CLOCK_HZ)
    tiles: StrictStr = DEFAULT_TILES
    reset: Line200
    pins: Pins = Field(default_factory=Pins)
    requirements: list[Requirement] = Field(min_length=1, max_length=100)
    open_questions: list[Block500] = Field(default_factory=list, max_length=50)

    @field_validator("version")
    @classmethod
    def _version_is_one(cls, value: int) -> int:
        if value != SPEC_VERSION:
            raise ValueError(f"only spec version {SPEC_VERSION} is supported")
        return value

    @field_validator("tiles")
    @classmethod
    def _tiles_allowed(cls, value: str) -> str:
        if value not in allowed_tiles():
            raise ValueError(unknown_tiles_message(value))
        return value

    def requirement_ids(self) -> list[str]:
        return [r.id for r in self.requirements]

    def to_dict(self) -> dict[str, Any]:
        """Plain data (pin bits stay integers; an absent limit or unit is left out)."""
        return self.model_dump(exclude_none=True)


@dataclass(frozen=True)
class SpecError:
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


# --- YAML ------------------------------------------------------------------------------------


class _SafeStrictLoader(yaml.SafeLoader):
    """`SafeLoader` that refuses anchors, aliases, merge keys and duplicate mapping keys."""

    def compose_node(self, parent: yaml.Node | None, index: int) -> yaml.Node | None:
        if self.check_event(yaml.AliasEvent):
            event = self.peek_event()  # type: ignore[no-untyped-call]
            raise yaml.constructor.ConstructorError(
                None, None, "aliases (*name) are not allowed in spec.yaml", event.start_mark
            )
        event = self.peek_event()  # type: ignore[no-untyped-call]
        if getattr(event, "anchor", None) is not None:
            raise yaml.constructor.ConstructorError(
                None, None, "anchors (&name) are not allowed in spec.yaml", event.start_mark
            )
        return super().compose_node(parent, index)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                raise yaml.constructor.ConstructorError(
                    None, None, "merge keys (<<) are not allowed in spec.yaml", key_node.start_mark
                )
            key = self.construct_object(key_node, deep=True)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                raise yaml.constructor.ConstructorError(
                    None, None, "a mapping key must be a plain value", key_node.start_mark
                ) from None
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
        return super().construct_mapping(node, deep=deep)


def _yaml_error(exc: yaml.YAMLError) -> SpecError:
    if isinstance(exc, yaml.MarkedYAMLError):
        mark = exc.problem_mark or exc.context_mark
        problem = exc.problem or exc.context or "YAML syntax error"
        if mark is not None:
            return SpecError("yaml", f"line {mark.line + 1}, column {mark.column + 1}: {problem}")
        return SpecError("yaml", str(problem))
    return SpecError("yaml", " ".join(str(exc).split()))


# --- error paths -----------------------------------------------------------------------------


def _format_loc(loc: tuple[int | str, ...]) -> tuple[str, str]:
    """Return `(path, shape)`: `pins.ui.8` / `requirements[2].id`, and the key-set lookup shape."""
    path = ""
    shape = ""
    parent = ""
    for part in loc:
        if not isinstance(part, int | str):
            part = str(part)
        if isinstance(part, int) and not path:
            path = str(part)
            shape = ""
        elif isinstance(part, int):
            if parent in ("requirements", "open_questions"):
                path += f"[{part}]"
                shape += "[]"
            else:
                path += f".{part}"
        else:
            path += f".{part}" if path else part
            shape += f".{part}" if shape else part
        parent = part if isinstance(part, str) else parent
    return path, shape


def _convert(err: Any) -> SpecError:
    loc = tuple(err["loc"])
    pin_key = bool(loc) and loc[-1] == "[key]"
    if pin_key:
        loc = loc[:-1]
    path, shape = _format_loc(loc)
    kind = err["type"]
    if pin_key:
        message = "pin bit must be an integer from 0 to 7"
    elif kind == "missing":
        message = "required field is missing"
    elif kind in ("extra_forbidden", "invalid_key"):
        if kind == "invalid_key":
            shape = f"{shape}.x" if shape else "x"
        parent = shape.rsplit(".", 1)[0] if "." in shape else ""
        allowed = _ALLOWED_KEYS.get(parent)
        message = "unknown key" + (f"; allowed keys here: {', '.join(allowed)}" if allowed else "")
    else:
        message = str(err["msg"]).removeprefix("Value error, ")
    return SpecError(path or "(file)", message)


def _duplicate_ids(spec: Spec) -> list[SpecError]:
    first: dict[str, int] = {}
    errors: list[SpecError] = []
    for index, req in enumerate(spec.requirements):
        if req.id in first:
            errors.append(
                SpecError(
                    f"requirements[{index}].id",
                    f"duplicate requirement id {req.id} "
                    f"(first used at requirements[{first[req.id]}])",
                )
            )
        else:
            first[req.id] = index
    return errors


def parse_spec(text: str) -> Spec | list[SpecError]:
    """Parse and validate `spec.yaml` text. Returns the `Spec`, or the list of errors found."""
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return [SpecError("(file)", "text is not valid UTF-8")]
    if size > MAX_SPEC_BYTES:
        return [SpecError("(file)", f"is {size} bytes; the limit is {MAX_SPEC_BYTES} bytes")]
    try:
        data = yaml.load(text, Loader=_SafeStrictLoader)
    except yaml.YAMLError as exc:
        return [_yaml_error(exc)]
    except RecursionError:
        return [SpecError("yaml", "nested too deeply")]
    except Exception as exc:  # a tag with a bad value, a huge integer, a bad date
        return [SpecError("yaml", " ".join(str(exc).split())[:200] or type(exc).__name__)]
    if not isinstance(data, dict):
        return [SpecError("(file)", "must be a YAML mapping with keys such as title, clock_hz")]
    try:
        spec = Spec.model_validate(data)
    except ValidationError as exc:
        return [_convert(e) for e in exc.errors(include_url=False)][:MAX_ERRORS]
    except (ValueError, TypeError, OverflowError) as exc:
        return [SpecError("(file)", " ".join(str(exc).split())[:200] or type(exc).__name__)]
    duplicates = _duplicate_ids(spec)
    return duplicates if duplicates else spec


def read_spec(project_dir: Path) -> tuple[Spec | None, str | None]:
    """Read and check `<project_dir>/spec.yaml`: `(spec, None)` or `(None, plain-English reason)`.

    The reason says "not found" when the file is missing, so callers can tell missing from bad.
    """
    try:
        text = safe_read(project_dir, SPEC_NAME, MAX_SPEC_BYTES).decode("utf-8")
    except UnicodeDecodeError:
        return None, "spec.yaml is not valid UTF-8 text"
    except ToolError:
        return None, "spec.yaml was not found or cannot be read"
    parsed = parse_spec(text)
    if isinstance(parsed, Spec):
        return parsed, None
    first = parsed[0]
    more = f" (+{len(parsed) - 1} more)" if len(parsed) > 1 else ""
    return None, f"{first.path}: {first.message}{more}"


def pinout(spec: Spec) -> dict[str, str]:
    """All 24 pins as Tiny Tapeout `info.yaml` keys (`ui[0]` ...), `""` where the spec is silent."""
    out: dict[str, str] = {}
    for group in PIN_GROUPS:
        meanings: dict[int, str] = getattr(spec.pins, group)
        for bit in range(8):
            out[f"{group}[{bit}]"] = meanings.get(bit, "")
    return out


# --- the starter spec written by create_project ------------------------------------------------

STUB_CLOCK_HZ = 10_000_000

_STUB_HEADER = """\
# spec.yaml: what this chip must do, in a short form you can read and judge.
#
# This is the one place that says the clock speed, what each pin means, and the numbered
# requirements (R1, R2, ...). Tests say which requirement they check with a docstring line
# such as "Covers: R1". Siliconsmith checks this file when it is saved with write_spec.
#
# THIS IS A PLACEHOLDER that describes the starter design (an adder). Replace it with the spec
# of the chip you really want.
"""


def stub_spec(title: str, summary: str) -> Spec:
    """The spec of the starter design: `uo_out = ui_in + uio_in`, modulo 256."""
    data: dict[str, Any] = {
        "version": SPEC_VERSION,
        "title": title,
        "summary": summary,
        "clock_hz": STUB_CLOCK_HZ,
        "reset": "rst_n, active-low, synchronous",
        "pins": {
            "ui": {b: f"first addend, bit {b}" for b in range(8)},
            "uo": {b: f"sum, bit {b}" for b in range(8)},
            "uio": {
                b: f"second addend, bit {b} (input only; the starter never drives uio)"
                for b in range(8)
            },
        },
        "requirements": [{"id": "R1", "text": "uo_out equals ui_in + uio_in, modulo 256"}],
        "open_questions": [
            "Confirm the clock speed (the starter assumes 10 MHz).",
            "Replace this starter spec with the spec of the real design.",
        ],
    }
    return Spec.model_validate(data)


def _q(text: str) -> str:
    """A double-quoted YAML scalar on one line."""
    dumped = yaml.safe_dump(text, default_style='"', allow_unicode=True, width=10**9)
    return dumped.removesuffix("\n...\n").removesuffix("\n")


def _num(value: int | float) -> str:
    """A YAML number that reads back as a number (floats keep a dot: 1.0e-07)."""
    return yaml.safe_dump(value, width=10**9).removesuffix("\n...\n").removesuffix("\n")


def render_spec(spec: Spec) -> str:
    """Deterministic spec.yaml text with a plain-English comment header (used for the stub)."""
    lines = [_STUB_HEADER, f"version: {spec.version}", f"title: {_q(spec.title)}"]
    lines.append(f"summary: {_q(spec.summary)}")
    lines.append(f"clock_hz: {spec.clock_hz}  # {spec.clock_hz / 1e6:g} MHz")
    lines.append(
        "# tiles: how much chip area the design rents on Tiny Tapeout. Bigger tiles cost more\n"
        "# money, so ask the owner before changing it. Allowed: " + ", ".join(allowed_tiles()) + "."
    )
    lines.append(f"tiles: {_q(spec.tiles)}")
    lines.append(f"reset: {_q(spec.reset)}")
    lines.append("")
    lines.append("# What each pin means. ui = inputs, uo = outputs, uio = bidirectional pins.")
    lines.append("pins:")
    for group in PIN_GROUPS:
        meanings: dict[int, str] = getattr(spec.pins, group)
        if not meanings:
            lines.append(f"  {group}: {{}}")
            continue
        lines.append(f"  {group}:")
        lines.extend(f"    {bit}: {_q(text)}" for bit, text in sorted(meanings.items()))
    lines.append("")
    lines.append("# Numbered requirements. Each test names the ones it checks (Covers: R1).")
    lines.append("requirements:")
    for req in spec.requirements:
        lines.append(f"  - id: {req.id}")
        lines.append(f"    text: {_q(req.text)}")
        if req.limit is not None:
            parts = [
                f"{k}: {_num(v)}"
                for k, v in req.limit.model_dump(exclude_none=True).items()
                if k != "unit"
            ]
            if req.limit.unit is not None:
                parts.append(f"unit: {_q(req.limit.unit)}")
            lines.append(f"    limit: {{{', '.join(parts)}}}")
    lines.append("")
    lines.append("# Things the owner still has to decide. Add your own assumptions here.")
    if spec.open_questions:
        lines.append("open_questions:")
        lines.extend(f"  - {_q(q)}" for q in spec.open_questions)
    else:
        lines.append("open_questions: []")
    return "\n".join(lines) + "\n"
