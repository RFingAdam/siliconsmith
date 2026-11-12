# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The validation ledger: `.siliconsmith/ledger.json` (PLAN.md section 5.7).

The ledger stores facts only: the owner's approval of the spec, and one record per
`emit_verilog` or `run_tests` run. It never says whether a step "passes"; `status.py` works that
out from these facts and the current files. Only the host writes it (the container never sees
`.siliconsmith`), and every write here is atomic. The caller holds `paths.project_lock`: the lock
is not re-entrant, so nothing in this module takes it.

A digest is a sha256 over a sorted list of `(relative path, sha256 of the bytes)` for an explicit
set of files, plus the image name and the Siliconsmith version. It never covers whole folders, so
build output, `__pycache__` and wave files cannot change it. The `check` digest covers what the
Verilog depends on; the `sim` digest adds the cocotb tests; the `spec` digest is the hash of
`spec.yaml`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from siliconsmith._version import __version__
from siliconsmith.paths import MissingFileError, atomic_write, safe_read, walk_files
from siliconsmith.project import Project
from siliconsmith.result import ToolResult, make_summary

logger = logging.getLogger("siliconsmith")

LEDGER_REL = ".siliconsmith/ledger.json"
CORRUPT_REL = ".siliconsmith/ledger.corrupt.json"
MAX_RUNS = 200
MAX_TESTS_PER_RUN = 500
MAX_LEDGER_BYTES = 8 * 1024 * 1024
MAX_HASHED_FILE_BYTES = 2 * 1024 * 1024
SPEC_REL = "spec.yaml"

Tool = Literal["emit_verilog", "run_tests"]
Simulator = Literal["icarus", "verilator"]
Outcome = Literal["pass", "fail", "skip", "error"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Approval(_Model):
    spec_sha256: str
    approved_by: str
    at: str


class RunTest(_Model):
    name: str
    module: str = ""
    covers: list[str] = Field(default_factory=list)
    outcome: Outcome


class Digests(_Model):
    check: str
    sim: str | None = None
    spec: str | None = None


class RunRecord(_Model):
    tool: Tool
    sim: Simulator | None = None
    at: str
    ok: bool
    stage: str
    filtered: bool = False
    image: str
    image_id: str | None = None
    siliconsmith_version: str
    digests: Digests
    tests: list[RunTest] = Field(default_factory=list)


class Ledger(_Model):
    version: Literal[1] = 1
    approval: Approval | None = None
    runs: list[RunRecord] = Field(default_factory=list)


@dataclass(frozen=True)
class LedgerLoad:
    ledger: Ledger
    # Plain-English reason the file was ignored, or None when it was read (or is simply absent).
    problem: str | None = None


def now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- reading and writing -------------------------------------------------------------------


def load_ledger(project_dir: Path) -> LedgerLoad:
    """Read the ledger. A missing file is an empty ledger; a bad one is empty plus a problem."""
    try:
        raw = safe_read(project_dir, LEDGER_REL, MAX_LEDGER_BYTES, allow_hidden=True)
    except MissingFileError:
        return LedgerLoad(Ledger())
    except ToolError as exc:
        return LedgerLoad(Ledger(), f"the ledger could not be read ({exc})")
    try:
        return LedgerLoad(Ledger.model_validate_json(raw))
    except (ValidationError, ValueError):
        return LedgerLoad(Ledger(), "the ledger is damaged (it is not valid ledger JSON)")


def _write(project_dir: Path, ledger: Ledger) -> None:
    ledger.runs = ledger.runs[-MAX_RUNS:]
    text = json.dumps(ledger.model_dump(mode="json"), indent=2) + "\n"
    atomic_write(project_dir, LEDGER_REL, text.encode("utf-8"), allow_hidden=True)


def _load_for_update(project_dir: Path) -> Ledger:
    """The ledger to add to. A damaged file is moved aside first and a fresh ledger starts."""
    loaded = load_ledger(project_dir)
    if loaded.problem is None:
        return loaded.ledger
    try:
        raw = safe_read(project_dir, LEDGER_REL, MAX_LEDGER_BYTES, allow_hidden=True)
        atomic_write(project_dir, CORRUPT_REL, raw, allow_hidden=True)
    except ToolError:
        logger.warning("could not keep a copy of the damaged ledger; replacing it")
    return Ledger()


def append_run(project_dir: Path, run: RunRecord) -> None:
    ledger = _load_for_update(project_dir)
    ledger.runs.append(run)
    _write(project_dir, ledger)


def set_approval(project_dir: Path, approval: Approval) -> None:
    ledger = _load_for_update(project_dir)
    ledger.approval = approval
    _write(project_dir, ledger)


# -- digests -------------------------------------------------------------------------------

_SKIP_DIRS = frozenset({"build", "sim_build", "__pycache__"})


def _file_hash(project_dir: Path, rel: str) -> str:
    try:
        return hashlib.sha256(safe_read(project_dir, rel, MAX_HASHED_FILE_BYTES)).hexdigest()
    except ToolError:
        return "unreadable"  # still changes the digest if the file appears or goes away


def spec_digest(project_dir: Path) -> str | None:
    """sha256 of `spec.yaml`, or None if it is missing or unreadable."""
    try:
        return hashlib.sha256(safe_read(project_dir, SPEC_REL, MAX_HASHED_FILE_BYTES)).hexdigest()
    except ToolError:
        return None


def check_inputs(project: Project, files: list[str]) -> list[str]:
    """Project files the generated Verilog and its lint result depend on."""
    wanted: list[str] = []
    for rel in files:
        if project.frontend == "verilog":
            hit = rel == f"src/{project.name}.v"
        else:
            hit = rel.startswith("hdl/") and rel.endswith(".py")
        if hit or rel in ("info.yaml", "test/tb.v", "test/Makefile"):
            wanted.append(rel)
    return wanted


def sim_inputs(project: Project, files: list[str]) -> list[str]:
    """The check inputs plus every cocotb test file (`test/**/*.py`, nested helpers too)."""
    tests = [r for r in files if r.startswith("test/") and r.endswith(".py")]
    return sorted({*check_inputs(project, files), *tests})


def _digest(project_dir: Path, rels: list[str], image: str) -> str:
    entries = [[rel, _file_hash(project_dir, rel)] for rel in sorted(rels)]
    blob = json.dumps({"files": entries, "image": image, "version": __version__}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_digests(project: Project, image: str) -> Digests:
    """The check, sim and spec digests of the project's current files."""
    files = [rel for rel, _ in walk_files(project.dir, skip_dirs=_SKIP_DIRS)]
    return Digests(
        check=_digest(project.dir, check_inputs(project, files), image),
        sim=_digest(project.dir, sim_inputs(project, files), image),
        spec=spec_digest(project.dir),
    )


# -- recording a tool run ------------------------------------------------------------------


def record_from_result(
    project: Project, image: str, tool: Tool, result: ToolResult, *, image_id: str | None = None
) -> RunRecord:
    """Turn a tool's result into a ledger record (computes the digests of the files now)."""
    details = result.details
    stage = "timeout" if details.get("timed_out") else str(details.get("stage", "unknown"))
    tests: list[RunTest] = []
    for item in details.get("tests", [])[:MAX_TESTS_PER_RUN]:
        tests.append(
            RunTest(
                name=str(item["name"]),
                module=str(item.get("module", "")),
                covers=[str(c) for c in item.get("covers", [])],
                outcome=item["outcome"],
            )
        )
    sim = details.get("sim") if tool == "run_tests" else None
    return RunRecord(
        tool=tool,
        sim=sim,
        at=now_utc(),
        ok=result.ok,
        stage=stage,
        filtered=bool(details.get("filtered", False)),
        image=image,
        image_id=image_id,
        siliconsmith_version=__version__,
        digests=compute_digests(project, image),
        tests=tests,
    )


def record_run(project: Project, image: str, tool: Tool, result: ToolResult) -> ToolResult:
    """Append the run to the ledger and return `result`.

    The caller holds the project lock (it is not re-entrant, so this takes none). A ledger
    problem never turns a result into an error: it is logged and noted in the summary.
    """
    try:
        append_run(project.dir, record_from_result(project, image, tool, result))
    except Exception as exc:  # the ledger must never break a tool result
        logger.warning("could not update the validation ledger: %s", exc)
        lines = [*result.summary.splitlines(), "Note: the validation ledger could not be updated."]
        result.summary = make_summary(lines)
    return result
