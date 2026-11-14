# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fail if a git-tracked file looks like a binary (magic number or NUL byte) or is over 1 MB.

Files listed in scripts/binary_allowlist.txt (one repo-relative path per line) are skipped.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

MAX_BYTES = 1_000_000
MAGICS: tuple[tuple[bytes, str], ...] = (
    (b"\x7fELF", "ELF executable"),
    (b"PK\x03\x04", "zip archive"),
    (b"PK\x05\x06", "zip archive"),
    (b"\x1f\x8b", "gzip"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"BZh", "bzip2"),
    (b"!<arch>\n", "ar archive"),
    (b"7z\xbc\xaf\x27\x1c", "7z archive"),
    (b"MZ", "Windows PE executable"),
    (b"\xcf\xfa\xed\xfe", "Mach-O executable"),
    (b"\xfe\xed\xfa\xcf", "Mach-O executable"),
    (b"\xca\xfe\xba\xbe", "Mach-O universal binary or Java class"),
    (b"\0asm", "WebAssembly module"),
)


def load_allowlist(path: Path) -> set[str]:
    if not path.exists():
        return set()
    entries = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.add(line)
    return entries


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [name.decode() for name in out.split(b"\0") if name]


def check_file(path: Path) -> str | None:
    """Return a reason the file is not allowed, or None."""
    if path.is_symlink() or not path.is_file():
        return None
    size = path.stat().st_size
    with path.open("rb") as handle:
        head = handle.read(8000)
    for magic, label in MAGICS:
        if head.startswith(magic):
            return label
    if head[257:262] == b"ustar":
        return "tar archive"
    if b"\0" in head:
        return "binary file (contains NUL bytes)"
    if size > MAX_BYTES:
        return f"larger than 1 MB ({size} bytes)"
    return None


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    allowed = load_allowlist(root / "scripts" / "binary_allowlist.txt")
    problems = []
    for name in tracked_files(root):
        if name in allowed:
            continue
        reason = check_file(root / name)
        if reason:
            problems.append(f"{name}: {reason}")
    for problem in problems:
        print(problem)
    if problems:
        print(
            "Do not commit tool binaries or archives. If a file is truly needed, "
            "list it in scripts/binary_allowlist.txt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
