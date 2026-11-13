# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Content-addressed tag for the toolchain image.

The tag is the first 12 hex characters of the sha256 of the exact output of
`git ls-files -s docker`. That output lists the mode, blob hash and path of every tracked file
under `docker/`, so the tag changes whenever any of those files change.

Usage: python -m siliconsmith._imagetag [--root DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

TAG_LENGTH = 12


def compute(repo_root: Path) -> str:
    proc = subprocess.run(
        ["git", "ls-files", "-s", "docker"],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"git ls-files failed in {repo_root}: {detail}")
    return hashlib.sha256(proc.stdout).hexdigest()[:TAG_LENGTH]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m siliconsmith._imagetag")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)
    try:
        print(compute(args.root))
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
