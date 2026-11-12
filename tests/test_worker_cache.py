# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Worker cache: real files, content-addressed, atomic."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from siliconsmith.runners import worker_cache as wc


def tree_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_cache_has_the_worker_files_as_real_files(tmp_path: Path) -> None:
    target = wc.ensure_worker_cache(home=tmp_path)
    files = tree_files(target)
    assert {
        "siliconsmith/__init__.py",
        "siliconsmith/_version.py",
        "siliconsmith/worker/__init__.py",
        "siliconsmith/worker/__main__.py",
        "siliconsmith/worker/info.py",
        "siliconsmith/worker/selftest.py",
    } <= files
    assert not any(p.is_symlink() for p in target.rglob("*"))
    assert not any("server" in name or "tools" in name for name in files)
    assert target.parent == tmp_path / "siliconsmith" / "worker"


def test_cache_is_reused_and_leaves_no_temp_dirs(tmp_path: Path) -> None:
    first = wc.ensure_worker_cache(home=tmp_path)
    second = wc.ensure_worker_cache(home=tmp_path)
    assert first == second
    assert [p.name for p in first.parent.iterdir()] == [first.name]


def make_package(root: Path, init: str = "") -> Path:
    package = root / "siliconsmith"
    (package / "worker").mkdir(parents=True)
    (package / "__init__.py").write_text(init)
    (package / "_version.py").write_text('__version__ = "1"\n')
    (package / "worker" / "__init__.py").write_text("")
    (package / "worker" / "__main__.py").write_text("x = 1\n")
    (package / "server.py").write_text("host only\n")
    return package


def test_different_content_gets_a_different_folder(tmp_path: Path) -> None:
    a = wc.ensure_worker_cache(home=tmp_path / "c", package=make_package(tmp_path / "a"))
    b = wc.ensure_worker_cache(home=tmp_path / "c", package=make_package(tmp_path / "b", "# new\n"))
    assert a != b
    assert a.is_dir() and b.is_dir()
    assert "siliconsmith/server.py" not in tree_files(a)


def test_symlinked_install_is_copied_as_real_files(tmp_path: Path) -> None:
    real = make_package(tmp_path / "real")
    linked_root = tmp_path / "linked" / "siliconsmith"
    linked_root.mkdir(parents=True)
    for name in ("__init__.py", "_version.py"):
        (linked_root / name).symlink_to(real / name)
    (linked_root / "worker").symlink_to(real / "worker")
    target = wc.ensure_worker_cache(home=tmp_path / "c", package=linked_root)
    assert not any(p.is_symlink() for p in target.rglob("*"))
    assert (target / "siliconsmith" / "worker" / "__main__.py").read_text() == "x = 1\n"


def test_race_loser_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = make_package(tmp_path / "p")
    winner = wc.ensure_worker_cache(home=tmp_path / "c", package=package)
    shutil.rmtree(winner)

    # Simulate the other process finishing first: create the target just before our rename.
    def racing_rename(self: Path, target: str | os.PathLike[str]) -> Path:
        Path(target).mkdir()
        raise OSError("exists")

    monkeypatch.setattr(Path, "rename", racing_rename)
    result = wc.ensure_worker_cache(home=tmp_path / "c", package=package)
    assert result == winner
    assert [p.name for p in winner.parent.iterdir()] == [winner.name]


def test_cache_home_uses_xdg() -> None:
    assert wc.cache_home({"XDG_CACHE_HOME": "/x"}) == Path("/x")
    assert wc.cache_home({}) == Path.home() / ".cache"
