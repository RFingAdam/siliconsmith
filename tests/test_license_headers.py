# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The license split by path, and the license of the files Siliconsmith generates.

Siliconsmith's own code is AGPL-3.0-or-later. The templates, the skills, the glossary and the
example designs stay Apache-2.0, and so does everything Siliconsmith writes into a user's project
(OUTPUT-EXCEPTION.md). A new file with the wrong license for its path fails here.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from siliconsmith.project import FRONTENDS
from siliconsmith.scaffold import create_project
from siliconsmith.worker import emit
from siliconsmith.wrapper import TT_PORTS, generate_wrapper

ROOT = Path(__file__).resolve().parent.parent
# Built in two pieces so that `reuse lint` does not read this file's own source as a header.
TAG = "SPDX-License-" + "Identifier:"
HEADER = re.compile(rf"^\s*(?:#|//|/\*|\*)\s*{TAG}\s*(.+?)\s*(?:\*/)?\s*$")

AGPL = "AGPL-3.0-or-later"
APACHE = "Apache-2.0"
# Keeps the upstream terms visible next to ours.
SPECIAL = {
    "src/siliconsmith/worker/synth_script.py": "AGPL-3.0-or-later AND Apache-2.0 AND ISC",
}
APACHE_PREFIXES = (
    "src/siliconsmith/templates/",
    "src/siliconsmith/skills/",
    "examples/",
)
APACHE_FILES = ("docs/glossary.md",)
# Third-party texts and captured records, which keep the terms stated in them.
SKIP_PREFIXES = ("docker/licenses/", "LICENSES/", "LICENSE")
# Captured generator output keeps the Apache-2.0 header the generator wrote, and the demo
# transcripts keep the headers of the generated files inside them. The scripts there are checked.
SPECIAL.update(
    {
        "tests/fixtures/synth/myhdl_latch_core.v": APACHE,
        "tests/fixtures/synth/myhdl_nested_core.v": APACHE,
    }
)


def skipped(rel: str) -> bool:
    return rel.startswith(SKIP_PREFIXES) or (rel.startswith("docs/demos/") and rel.endswith(".md"))


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(f for f in out.splitlines() if (ROOT / f).is_file())


def expected(rel: str) -> str:
    if rel in SPECIAL:
        return SPECIAL[rel]
    if rel.startswith(APACHE_PREFIXES) or rel in APACHE_FILES:
        return APACHE
    return AGPL


def own_header(rel: str) -> str | None:
    try:
        lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()[:6]
    except (UnicodeDecodeError, OSError):
        return None
    for line in lines:
        m = HEADER.match(line)
        if m:
            return m.group(1)
    return None


def glob_regex(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + "$")


def reuse_license(rel: str) -> str | None:
    """License that REUSE.toml gives a file: the last matching table wins."""
    data = tomllib.loads((ROOT / "REUSE.toml").read_text())
    found = None
    for table in data["annotations"]:
        paths = table["path"] if isinstance(table["path"], list) else [table["path"]]
        if any(glob_regex(p).match(rel) for p in paths):
            found = table["SPDX-License-Identifier"]
    return found


def test_headers_match_the_license_split_by_path() -> None:
    wrong = []
    for rel in tracked_files():
        if skipped(rel):
            continue
        header = own_header(rel)
        if header is not None and header != expected(rel):
            wrong.append(f"{rel}: {header}, expected {expected(rel)}")
    assert not wrong, "\n".join(wrong)


def test_python_files_have_a_header() -> None:
    missing = [
        rel
        for rel in tracked_files()
        if rel.endswith(".py")
        and not skipped(rel)
        and not rel.startswith("tests/fixtures/")
        and own_header(rel) is None
    ]
    assert not missing, missing


def test_files_without_a_header_follow_reuse_toml() -> None:
    wrong = []
    for rel in tracked_files():
        if skipped(rel) or own_header(rel) is not None:
            continue
        got = reuse_license(rel)
        if got is None:
            wrong.append(f"{rel}: no REUSE.toml table")
        elif rel.startswith("src/siliconsmith/templates/tt"):
            if got != APACHE:
                wrong.append(f"{rel}: {got}")
        elif got != expected(rel):
            wrong.append(f"{rel}: {got}, expected {expected(rel)}")
    assert not wrong, "\n".join(wrong)


def test_license_texts_are_present() -> None:
    for name in ("AGPL-3.0-or-later", "Apache-2.0", "ISC"):
        assert (ROOT / "LICENSES" / f"{name}.txt").is_file()
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in (ROOT / "LICENSE").read_text()
    assert (ROOT / "OUTPUT-EXCEPTION.md").is_file()


GENERATOR_MODULES = [
    "scaffold.py",
    "wrapper.py",
    "status.py",
    "worker/wrapper_gen.py",
    "worker/emit.py",
]


@pytest.mark.parametrize("module", GENERATOR_MODULES)
def test_generator_modules_point_to_the_output_exception(module: str) -> None:
    head = (ROOT / "src" / "siliconsmith" / module).read_text().split("\n\n")[0]
    assert "OUTPUT-EXCEPTION.md" in head


def test_header_pattern_reads_headers() -> None:
    assert HEADER.match(f"# {TAG} {AGPL}")
    assert HEADER.match(f" * {TAG} {APACHE}")
    assert HEADER.match(f"// {TAG} {APACHE}")


def _licenses(text: str) -> list[str]:
    return [m.group(1) for m in map(HEADER.match, text.splitlines()) if m]


@pytest.mark.parametrize("frontend", FRONTENDS)
def test_generated_project_files_are_apache(tmp_path: Path, frontend: str) -> None:
    project, _ = create_project(tmp_path, "chip", frontend, "Ada Lovelace", "Adds numbers.")
    checked = 0
    for rel in ("src/project.v", "test/test.py", "src/chip.v", "hdl/chip.py"):
        path = project.dir / rel
        if path.exists():
            assert _licenses(path.read_text()) == [APACHE], rel
            checked += 1
    assert checked >= 3


def test_generated_wrapper_and_core_header_are_apache() -> None:
    ports = {port: direction for port, (direction, _) in TT_PORTS.items()}
    assert _licenses(generate_wrapper("chip", ports, "Ada Lovelace")) == [APACHE]
    assert _licenses(emit._core_header("Ada Lovelace", "chip")) == [APACHE]
