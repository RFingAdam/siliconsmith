# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Helpers for integration tests: run commands in the image with the runner's own flags."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from siliconsmith.runners.docker import build_argv, readonly_entries
from siliconsmith.runners.worker_cache import ensure_worker_cache


def hardened(image: str, project: Path, *command: str, mount: Path | None = None) -> list[str]:
    """The runner's own `docker run` line (build_argv), with `command` in place of the worker."""
    (project / "build").mkdir(exist_ok=True)
    argv = build_argv(
        container_name=f"ss-test-{uuid.uuid4().hex}",
        image=image,
        verb="unused",
        project_dir=project,
        readonly_entries=readonly_entries(project),
        worker_cache=ensure_worker_cache(),
        user=None if os.getuid() == 0 else (os.getuid(), os.getgid()),
    )
    assert argv[-5:-1] == [image, "python", "-m", "siliconsmith.worker"]
    head = argv[:-5]
    if mount is not None:
        head += ["--mount", f"type=bind,src={mount},dst=/smoke,readonly"]
    return [*head, image, *command]


def run_hardened(
    image: str, project: Path, *command: str, mount: Path | None = None, timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        hardened(image, project, *command, mount=mount),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        check=False,
    )
