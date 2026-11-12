# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Runner interface: how the server executes one worker verb."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class WorkerOutcome:
    exit_code: int
    result: dict[str, Any] | None
    stdout_tail: str
    stderr_tail: str
    timed_out: bool


class Runner(Protocol):
    def run(
        self,
        verb: str,
        request: Mapping[str, Any],
        *,
        project_dir: Path,
        timeout_s: float,
    ) -> WorkerOutcome: ...
