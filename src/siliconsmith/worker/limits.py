# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Size limits for the `emit` result, shared by the worker (which clips) and the host (which
enforces them with pydantic). One place, so the two sides cannot drift apart.

Stdlib only: this runs in the container.
"""

from __future__ import annotations

from typing import Any

MESSAGE_MAX = 2000
FINDING_MSG_MAX = 600
FINDING_DETAIL_MAX = 800
FINDING_CODE_MAX = 40
FILE_MAX = 200
PYERR_KIND_MAX = 100
PYERR_MSG_MAX = 800
PYERR_SOURCE_MAX = 300
MAX_FINDINGS = 200
# A generated core larger than this is refused (the host copies it into the project).
MAX_GENERATED_BYTES = 256 * 1024


def clip(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters, with a trailing `…` when something was cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clip_fields(item: dict[str, Any], limits: dict[str, int]) -> None:
    for key, limit in limits.items():
        if isinstance(item.get(key), str):
            item[key] = clip(item[key], limit)


def clamp_result(result: dict[str, Any]) -> dict[str, Any]:
    """Clip every text field of an `emit` result to the host's limits (in place, and returned)."""
    if isinstance(result.get("message"), str):
        result["message"] = clip(result["message"], MESSAGE_MAX)
    for finding in result.get("lint") or []:
        _clip_fields(
            finding,
            {
                "code": FINDING_CODE_MAX,
                "file": FILE_MAX,
                "msg": FINDING_MSG_MAX,
                "detail": FINDING_DETAIL_MAX,
                "hdl_file": FILE_MAX,
                "hdl_function": SYNTH_SIGNAL_MAX,
            },
        )
    error = result.get("python_error")
    if isinstance(error, dict):
        _clip_fields(
            error,
            {
                "kind": PYERR_KIND_MAX,
                "file": FILE_MAX,
                "msg": PYERR_MSG_MAX,
                "source": PYERR_SOURCE_MAX,
            },
        )
    return result


# `run_tests` and `inspect_waves` results (clipped in the worker, enforced again on the host).
TEST_NAME_MAX = 200
TEST_TEXT_MAX = 300
TEST_VALUE_MAX = 120
MAX_TESTS = 500
MAX_LOG_LINES = 15
LOG_LINE_MAX = 300
WAVES_PATH_MAX = 200
WAVE_SIGNAL_MAX = 300
WAVE_VALUE_MAX = 80
MAX_WAVE_ROWS = 1000
MAX_WAVE_SIGNALS = 16
MAX_SUGGESTIONS = 8

# What the host reads back from a generated core before it decides it is too large.
MAX_CORE_READ_BYTES = 1024 * 1024

# The `synth` verb's own result (clipped in the worker, enforced again on the host).
MAX_SYNTH_FINDINGS = 50
SYNTH_SIGNAL_MAX = 200
MAX_TOP_CELLS = 10
CELL_NAME_MAX = 100
MAX_FORBIDDEN_CELLS = 20
MAX_SYNTH_LOG_LINES = 12
