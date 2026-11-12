# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Copy the in-container worker code to a per-version cache folder that the container mounts.

The container gets `siliconsmith/__init__.py`, `siliconsmith/_version.py` and everything under
`siliconsmith/worker/` as real files, never symlinks, because an editable or symlinked install
would otherwise leave dangling links inside the container. The folder name is a hash of the file
contents, so a new Siliconsmith version gets a new folder and an old one is never edited.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import siliconsmith

_TOP_LEVEL_FILES = ("__init__.py", "_version.py")


def package_dir() -> Path:
    return Path(siliconsmith.__file__).resolve().parent


def worker_files(package: Path) -> dict[str, bytes]:
    """Map of path (relative to the folder that contains `siliconsmith/`) to file contents."""
    files: dict[str, bytes] = {}
    for name in _TOP_LEVEL_FILES:
        files[f"siliconsmith/{name}"] = (package / name).read_bytes()
    worker = package / "worker"
    for path in sorted(worker.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        files[f"siliconsmith/worker/{path.relative_to(worker).as_posix()}"] = path.read_bytes()
    return files


def content_hash(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode() + b"\0")
        digest.update(hashlib.sha256(files[name]).digest())
    return digest.hexdigest()[:16]


def cache_home(env: dict[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    base = environ.get("XDG_CACHE_HOME")
    return Path(base) if base else Path.home() / ".cache"


def ensure_worker_cache(home: Path | None = None, package: Path | None = None) -> Path:
    """Return the folder to mount at `/opt/ss`, creating it if this version is not cached yet.

    The folder is built in a temporary sibling and renamed into place, so a reader never sees a
    half-written cache and two servers starting at once cannot corrupt each other.
    """
    files = worker_files(package or package_dir())
    parent = (home or cache_home()) / "siliconsmith" / "worker"
    target = parent / content_hash(files)
    if target.is_dir():
        return target
    parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=parent))
    try:
        for name, data in files.items():
            dest = tmp / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(0o644)
        for directory in [tmp, *(p for p in tmp.rglob("*") if p.is_dir())]:
            directory.chmod(0o755)
        try:
            tmp.rename(target)
        except OSError:
            if not target.is_dir():
                raise
            # Another process won the race; its folder has the same content.
            shutil.rmtree(tmp, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target
