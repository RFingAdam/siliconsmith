# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

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
    # The run folder the runner made, `build/.ss/<run_id>`. The container's own claim is not used.
    run_id: str | None = None


class Runner(Protocol):
    def run(
        self,
        verb: str,
        request: Mapping[str, Any],
        *,
        project_dir: Path,
        timeout_s: float,
    ) -> WorkerOutcome: ...
