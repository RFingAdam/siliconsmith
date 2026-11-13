# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fixtures for tests that need Docker and the toolchain image."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from siliconsmith.runners.docker import DockerRunner, image_name


@pytest.fixture(scope="session")
def image() -> str:
    """The image under test: SILICONSMITH_IMAGE, else the default. Skips if it is not local."""
    name = image_name()
    if shutil.which("docker") is None:
        pytest.skip("docker is not installed")
    proc = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.skip(f"image {name} is not present locally; run `docker pull {name}` or build it")
    return name


@pytest.fixture(scope="session")
def runner(image: str) -> DockerRunner:
    return DockerRunner(image)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project folder with every read-only entry the runner mounts."""
    path = tmp_path / "proj"
    for name in ("hdl", "src", "test"):
        (path / name).mkdir(parents=True)
        (path / name / "keep.txt").write_text("x")
    (path / "spec.yaml").write_text("title: t\n")
    (path / "info.yaml").write_text("title: t\n")
    return path
