# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared state handed to every tool module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from siliconsmith.runners.base import Runner
from siliconsmith.runners.docker import DEFAULT_IMAGE, DockerRunner


@dataclass(frozen=True)
class ServerContext:
    root: Path
    default_frontend: str | None
    # The toolchain image name, and the runner that starts it. `build_server` always fills both;
    # tests pass a fake runner.
    image: str = DEFAULT_IMAGE
    runner: Runner | None = None

    def get_runner(self) -> Runner:
        """The runner tools should use (a Docker runner for `image` if none was injected)."""
        return self.runner if self.runner is not None else DockerRunner(self.image)
