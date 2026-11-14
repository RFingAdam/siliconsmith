# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Build the wheel and check that every git-tracked file under src/siliconsmith/ is in it.

Also checks the wheel's license metadata and that the license files are inside it.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGE_DIR = "src/siliconsmith/"
# Package data the server cannot work without: the skills and the glossary that `explain` reads.
REQUIRED_SKILLS = (
    "chip-design-loop",
    "tt-rules",
    "amaranth-style",
    "myhdl-style",
    "cocotb2-tests",
)
REQUIRED_FILES = (
    *(f"siliconsmith/skills/{name}/SKILL.md" for name in REQUIRED_SKILLS),
    "siliconsmith/skills/glossary.md",
)


def tracked_package_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", PACKAGE_DIR], cwd=root, capture_output=True, check=True
    ).stdout
    names = [name.decode() for name in out.split(b"\0") if name]
    return [n.removeprefix("src/") for n in names if not n.endswith(".pyc")]


def expected_files(root: Path) -> list[str]:
    """Every tracked package file, plus the skills and glossary even if not yet tracked."""
    names = tracked_package_files(root)
    on_disk = (root / PACKAGE_DIR).glob("skills/*/SKILL.md")
    extra = [str(p.relative_to(root / "src")) for p in on_disk]
    return sorted({*names, *REQUIRED_FILES, *extra})


def missing_from_wheel(expected: list[str], wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        present = set(zf.namelist())
    return [name for name in expected if name not in present]


LICENSE_EXPRESSION = "AGPL-3.0-or-later"
LICENSE_FILES = (
    "LICENSE",
    "NOTICE",
    "OUTPUT-EXCEPTION.md",
    "LICENSES/AGPL-3.0-or-later.txt",
    "LICENSES/Apache-2.0.txt",
    "LICENSES/ISC.txt",
)


def license_problems(wheel: Path) -> list[str]:
    """Problems with the wheel's license expression and the license files it carries."""
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        metadata = [n for n in names if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            return ["wheel has no single METADATA file"]
        text = zf.read(metadata[0]).decode()
    problems = []
    if f"License-Expression: {LICENSE_EXPRESSION}\n" not in text:
        problems.append(f"METADATA does not say License-Expression: {LICENSE_EXPRESSION}")
    prefix = metadata[0].removesuffix("METADATA") + "licenses/"
    problems += [
        f"license file missing from wheel: {name}"
        for name in LICENSE_FILES
        if prefix + name not in names
    ]
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["uv", "build", "--wheel", "--out-dir", tmp], cwd=root, check=True)
        wheels = sorted(Path(tmp).glob("*.whl"))
        if len(wheels) != 1:
            print(f"expected one wheel, found {len(wheels)}", file=sys.stderr)
            return 1
        missing = missing_from_wheel(expected_files(root), wheels[0])
        problems = license_problems(wheels[0])
    for name in missing:
        print(f"missing from wheel: {name}")
    for problem in problems:
        print(problem)
    if missing or problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
