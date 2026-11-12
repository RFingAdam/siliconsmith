# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Build the wheel and check that every git-tracked file under src/siliconsmith/ is in it."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PACKAGE_DIR = "src/siliconsmith/"


def tracked_package_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--", PACKAGE_DIR], cwd=root, capture_output=True, check=True
    ).stdout
    names = [name.decode() for name in out.split(b"\0") if name]
    return [n.removeprefix("src/") for n in names if not n.endswith(".pyc")]


def missing_from_wheel(expected: list[str], wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        present = set(zf.namelist())
    return [name for name in expected if name not in present]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["uv", "build", "--wheel", "--out-dir", tmp], cwd=root, check=True)
        wheels = sorted(Path(tmp).glob("*.whl"))
        if len(wheels) != 1:
            print(f"expected one wheel, found {len(wheels)}", file=sys.stderr)
            return 1
        missing = missing_from_wheel(tracked_package_files(root), wheels[0])
    for name in missing:
        print(f"missing from wheel: {name}")
    if missing:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
