# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from siliconsmith import _imagetag


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def test_no_docker_dir_hashes_empty_output(git_repo: Path) -> None:
    assert _imagetag.compute(git_repo) == hashlib.sha256(b"").hexdigest()[:12]


def test_tag_matches_expected_hash(git_repo: Path) -> None:
    (git_repo / "docker").mkdir()
    (git_repo / "docker" / "Dockerfile").write_text("FROM scratch\n")
    _git(git_repo, "add", "docker")
    listing = _git(git_repo, "ls-files", "-s", "docker")
    assert listing.startswith("100644 ")
    expected = hashlib.sha256(listing.encode()).hexdigest()[:12]
    assert _imagetag.compute(git_repo) == expected
    assert _imagetag.compute(git_repo) == expected  # deterministic


def test_tag_changes_with_content(git_repo: Path) -> None:
    (git_repo / "docker").mkdir()
    f = git_repo / "docker" / "Dockerfile"
    f.write_text("FROM scratch\n")
    _git(git_repo, "add", "docker")
    first = _imagetag.compute(git_repo)
    f.write_text("FROM scratch\nRUN true\n")
    _git(git_repo, "add", "docker")
    assert _imagetag.compute(git_repo) != first


def test_untracked_files_are_ignored(git_repo: Path) -> None:
    (git_repo / "docker").mkdir()
    (git_repo / "docker" / "Dockerfile").write_text("x")
    assert _imagetag.compute(git_repo) == hashlib.sha256(b"").hexdigest()[:12]


def test_module_cli_prints_tag(git_repo: Path) -> None:
    out = subprocess.run(
        [sys.executable, "-m", "siliconsmith._imagetag", "--root", str(git_repo)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert out == _imagetag.compute(git_repo)
    assert len(out) == 12
