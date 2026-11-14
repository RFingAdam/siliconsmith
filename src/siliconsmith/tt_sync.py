# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Under section 7 of the GNU AGPL version 3, you are granted the additional
# permission in OUTPUT-EXCEPTION.md: files this module writes into a user's
# project, and the text it copies into them, may be used under Apache-2.0.

"""Keep the Tiny Tapeout files in step with `spec.yaml` (PLAN.md section 5.7).

`spec.yaml` is the single source of the clock speed, the tile size and the pin meanings. Tiny
Tapeout reads the same facts from two other files, and the tools (never the agent) copy them over:

- `src/config.json`: `CLOCK_PERIOD` (nanoseconds, = 1e9 / `clock_hz`). It is changed by a text
  patch of that one line, never by loading and saving the JSON, so Tiny Tapeout's many duplicate
  `"//"` comment keys and its layout survive.
- `info.yaml`: `project.clock_hz`, `project.tiles` and the `pinout` block.

`sync_spec_files` is called by `write_spec` after a valid write, and by the tools that run the
flow (`synthesize`, ...) under the project lock just before they take their digest snapshot, so
projects made before this existed are fixed too. `create_project` uses the same pure functions
(`patch_config_json`, `render_info_yaml`) when it writes the first files. The caller holds
`paths.project_lock`; nothing here takes it.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.paths import MissingFileError, atomic_write, safe_read
from siliconsmith.project import Project
from siliconsmith.spec import SPEC_NAME, Spec, pinout, read_spec

CONFIG_REL = "src/config.json"
INFO_REL = "info.yaml"
MAX_FILE_BYTES = 256 * 1024
# Tiny Tapeout's pin interface is limited to about 66 MHz (the clock the chip can be given).
TT_IO_LIMIT_HZ = 66_000_000

INFO_HEADER = (
    "# Tiny Tapeout project information (written by Siliconsmith).\n"
    "# clock_hz, tiles and pinout are copied from spec.yaml by the Siliconsmith tools\n"
    "# (create_project, write_spec and synthesize). To change them, edit spec.yaml with\n"
    "# write_spec, not this file.\n"
)

# One `"CLOCK_PERIOD": 20,` line. Anything else on the line (a trailing comma) is kept.
_CLOCK_LINE = re.compile(r'^(?P<head>[ \t]*"CLOCK_PERIOD"[ \t]*:[ \t]*)(?P<value>[^,\s}]+)', re.M)


class SyncError(ValueError):
    """A Tiny Tapeout file cannot be patched (it is not the shape the pinned template has)."""


def clock_period_ns(clock_hz: int) -> int | float:
    """`CLOCK_PERIOD` for `clock_hz`: 1e9 / clock_hz, rounded to 3 decimals, an int when whole."""
    ns = round(1e9 / clock_hz, 3)
    return int(ns) if ns == int(ns) else ns


def clock_warnings(clock_hz: int) -> list[str]:
    """Plain-English warnings about the clock speed (empty when it is fine)."""
    if clock_hz <= TT_IO_LIMIT_HZ:
        return []
    return [
        f"clock_hz is {clock_hz / 1e6:g} MHz, above Tiny Tapeout's pin limit of about "
        f"{TT_IO_LIMIT_HZ // 1_000_000} MHz. The chip's input and output pins cannot keep up "
        "with a clock that fast, so lower clock_hz unless the design only uses it internally."
    ]


def patch_config_json(text: str, clock_hz: int) -> str:
    """`text` with the `CLOCK_PERIOD` line set from `clock_hz` and nothing else changed."""
    matches = list(_CLOCK_LINE.finditer(text))
    if len(matches) != 1:
        raise SyncError(
            f'{CONFIG_REL} must contain exactly one "CLOCK_PERIOD" line; found {len(matches)}'
        )
    m = matches[0]
    return text[: m.start("value")] + str(clock_period_ns(clock_hz)) + text[m.end("value") :]


def render_info_yaml(info: dict[str, Any]) -> str:
    return INFO_HEADER + yaml.safe_dump(info, sort_keys=False, allow_unicode=True)


def patch_info_yaml(text: str, spec: Spec) -> str:
    """`info.yaml` text with `clock_hz`, `tiles` and `pinout` copied from `spec`."""
    try:
        info = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SyncError(
            f"{INFO_REL} is not valid YAML ({' '.join(str(exc).split())[:120]})"
        ) from exc
    if not isinstance(info, dict) or not isinstance(info.get("project"), dict):
        raise SyncError(f"{INFO_REL} has no project: section")
    info["project"]["clock_hz"] = spec.clock_hz
    info["project"]["tiles"] = spec.tiles
    info["pinout"] = pinout(spec)
    return render_info_yaml(info)


@dataclass
class SyncResult:
    """What `sync_spec_files` found.

    `ok` is False when the spec is missing or invalid or a Tiny Tapeout file cannot be patched;
    `problem` then says why. `spec` is set whenever spec.yaml was valid. `changed` lists the files
    that were rewritten. `warnings` holds clock warnings (always computed from a valid spec).
    """

    ok: bool
    problem: str | None = None
    spec: Spec | None = None
    changed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _read_text(project_dir: Path, rel: str) -> str:
    try:
        return safe_read(project_dir, rel, MAX_FILE_BYTES).decode("utf-8")
    except MissingFileError:
        raise SyncError(f"{rel} is missing from the project") from None
    except (ToolError, UnicodeDecodeError) as exc:
        raise SyncError(f"{rel} cannot be read ({exc})") from exc


def sync_spec_files(project: Project) -> SyncResult:
    """Copy the clock, tiles and pinout from spec.yaml into src/config.json and info.yaml.

    Never raises for a bad spec or a bad Tiny Tapeout file: it returns `ok=False` and a reason.
    Files are written only when their text changes. The caller holds the project lock.
    """
    spec, why = read_spec(project.dir)
    if spec is None:
        return SyncResult(False, f"{why}. Fix {SPEC_NAME} with write_spec.")
    result = SyncResult(True, spec=spec, warnings=clock_warnings(spec.clock_hz))
    patches: list[tuple[str, Callable[[str], str]]] = [
        (CONFIG_REL, lambda text: patch_config_json(text, spec.clock_hz)),
        (INFO_REL, lambda text: patch_info_yaml(text, spec)),
    ]
    try:
        for rel, patch in patches:
            old = _read_text(project.dir, rel)
            new = patch(old)
            if new != old:
                atomic_write(project.dir, rel, new.encode("utf-8"))
                result.changed.append(rel)
    except SyncError as exc:
        result.ok = False
        result.problem = str(exc)
    except ToolError as exc:
        result.ok = False
        result.problem = f"could not update the Tiny Tapeout files ({exc})"
    return result


def files_match_spec(project: Project, spec: Spec) -> bool:
    """True when src/config.json and info.yaml already carry the spec's clock and tiles.

    Read-only, for `status`. A file that cannot be read or patched counts as matching: the synth
    digest already covers its content, and the next synthesize reports the real problem.
    """
    patches: list[tuple[str, Callable[[str], str]]] = [
        (CONFIG_REL, lambda text: patch_config_json(text, spec.clock_hz)),
        (INFO_REL, lambda text: patch_info_yaml(text, spec)),
    ]
    for rel, patch in patches:
        try:
            text = _read_text(project.dir, rel)
            if patch(text) != text:
                return False
        except (SyncError, ToolError):
            return True
    return True


def sync_for_synthesis(project: Project) -> Spec:
    """Sync, then return the valid spec; raise a `ToolError` an agent can act on if it cannot.

    For the tools that run the flow, under the project lock and before the digest snapshot.
    """
    result = sync_spec_files(project)
    if not result.ok or result.spec is None:
        raise ToolError(f"cannot continue: {result.problem}")
    return result.spec
