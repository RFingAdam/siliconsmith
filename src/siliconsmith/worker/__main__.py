# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Container entry point: `python -m siliconsmith.worker <verb>`.

The request is JSON on stdin. The worker writes exactly one result JSON file, atomically, at the
`result_path` the host chose under `/work/build`, and exits 0 when it wrote it. A failure inside
a verb still produces a result, with an `error` entry that has a `kind` and a `message`.
Standard output and standard error are free-form logs: the host never parses them.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

BUILD_DIR = "/work/build"


def _verb_info(request: dict[str, Any]) -> dict[str, Any]:
    from siliconsmith.worker import info

    return info.collect()


def _verb_selftest(request: dict[str, Any]) -> dict[str, Any]:
    from siliconsmith.worker import selftest

    return selftest.run(request)


VERBS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "info": _verb_info,
    "selftest": _verb_selftest,
}


class RequestError(Exception):
    """The request on stdin was unusable."""


def check_result_path(value: object, build_dir: str = BUILD_DIR) -> str:
    """Return `value` if it is a normalized absolute path strictly inside `build_dir`."""
    if not isinstance(value, str) or not value or "\x00" in value:
        raise RequestError("result_path must be a non-empty string")
    normalized = os.path.normpath(value)
    if normalized != value or not os.path.isabs(value):
        raise RequestError(f"result_path {value!r} must be absolute and normalized")
    if os.path.commonpath([value, build_dir]) != build_dir or value == build_dir:
        raise RequestError(f"result_path {value!r} is outside {build_dir}")
    return value


def write_result(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def read_request(stream: Any) -> dict[str, Any]:
    try:
        request = json.loads(stream.read())
    except ValueError as exc:
        raise RequestError(f"request is not valid JSON: {exc}") from exc
    if not isinstance(request, dict):
        raise RequestError("request must be a JSON object")
    return request


def main(argv: Sequence[str] | None = None, stdin: Any = None, build_dir: str = BUILD_DIR) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m siliconsmith.worker <verb>", file=sys.stderr)
        return 2
    verb = args[0]
    try:
        request = read_request(sys.stdin if stdin is None else stdin)
        result_path = check_result_path(request.get("result_path"), build_dir)
    except RequestError as exc:
        print(f"worker: {exc}", file=sys.stderr)
        return 2
    os.environ.setdefault("HOME", "/tmp/home")
    os.makedirs(os.environ["HOME"], exist_ok=True)
    handler = VERBS.get(verb)
    if handler is None:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {"kind": "unknown_verb", "message": f"unknown verb {verb!r}"},
        }
    else:
        try:
            payload = {"ok": True, "verb": verb, **handler(request)}
        except Exception as exc:
            traceback.print_exc()
            payload = {
                "ok": False,
                "verb": verb,
                "error": {"kind": type(exc).__name__, "message": str(exc)},
            }
    write_result(result_path, payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
