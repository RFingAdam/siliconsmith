# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Fail if a pull request from a fork does not have the CLA box ticked.

Usage: python scripts/check_cla.py

Reads env PR_BODY (the pull request description), HEAD_REPO and BASE_REPO. A PR from a branch of
this repository (the owner's and agents') is exempt, and so is Dependabot (the workflow skips
this step for it). A PR is from a fork when HEAD_REPO and BASE_REPO are both set and differ; this
is the same rule as check_trailers.py, which this script loads for it.

If HEAD_REPO is empty but BASE_REPO is set, the fork was probably deleted, so the box is still
required.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

# A ticked checklist item that mentions the CLA: "- [x] I have read and agree to the CLA ...".
TICKED = re.compile(r"^\s*[-*+]\s*\[[xX]\]\s.*\bCLA\b")


def _load_trailers() -> ModuleType:
    path = Path(__file__).resolve().parent / "check_trailers.py"
    spec = importlib.util.spec_from_file_location("check_trailers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def cla_ticked(body: str) -> bool:
    """True if the box is ticked outside any HTML comment (a commented-out box does not count)."""
    body = COMMENT.sub("", body)
    return any(TICKED.match(line) for line in body.splitlines())


def needs_cla(env: Mapping[str, str]) -> bool:
    """True when the PR comes from a fork, or from a head repository that no longer exists."""
    if _load_trailers().is_fork_pr(env):
        return True
    return bool(env.get("BASE_REPO") and not env.get("HEAD_REPO"))


def main() -> int:
    if not os.environ.get("BASE_REPO"):
        print("BASE_REPO is not set; nothing to check", file=sys.stderr)
        return 2
    if not needs_cla(os.environ):
        print("PR is from a branch of this repository; the CLA box is not required")
        return 0
    if cla_ticked(os.environ.get("PR_BODY", "")):
        print("CLA box is ticked")
        return 0
    print(
        "This pull request is from a fork. Tick the CLA box in the description: "
        "'- [x] I have read and agree to the CLA ...' (see CLA.md).",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
