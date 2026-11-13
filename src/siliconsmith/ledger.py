# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The validation ledger: `.siliconsmith/ledger.json` (PLAN.md section 5.7).

The ledger stores facts only: the owner's approval of the spec, and one record per
`emit_verilog`, `run_tests` or `synthesize` run (a gate-level `run_tests` run is marked `gl` and
carries both the `sim` and the `synth` digests, taken before the run). It never says whether a
step "passes"; `status.py` works that out from these facts and the current files. Only the host
writes it (the container never sees `.siliconsmith`), and every write here is atomic. The caller
holds `paths.project_lock`: the lock is not re-entrant, so nothing in this module takes it.

A digest is a sha256 over a sorted list of `(relative path, sha256 of the bytes)` for an explicit
set of files, plus the image name and the Siliconsmith version. It never covers whole folders, so
build output, `__pycache__` and wave files cannot change it. The `check` digest covers what the
Verilog depends on; the `sim` digest adds the cocotb tests; the `synth` digest is the check
inputs plus `src/config.json` and `info.yaml` (the clock and tile size do not change the RTL, but
they change synthesis); the `spec` digest is the hash of `spec.yaml`.
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
# _write trims the oldest runs to stay under this, so load_ledger never refuses what we wrote.
WRITE_LEDGER_BYTES = 6 * 1024 * 1024
# Reading a damaged ledger only to move it aside or rescue the approval: allow it to be big.
RESCUE_LEDGER_BYTES = 256 * 1024 * 1024
MAX_HASHED_FILE_BYTES = 2 * 1024 * 1024
SPEC_REL = "spec.yaml"

Tool = Literal["emit_verilog", "run_tests", "synthesize"]
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
    synth: str | None = None  # absent in ledgers written before M1


class SynthFacts(_Model):
    """Facts of a `synthesize` run (all optional: a run can stop before there is a netlist)."""

    cells: int | None = None
    flip_flops: int | None = None
    area_um2: float | None = None
    utilization: float | None = None  # the estimate: cell area / cell budget
    tiles: str | None = None
    verdict: str | None = None  # ok | warn | too_big
    netlist_sha256: str | None = None


class RunRecord(_Model):
    tool: Tool
    sim: Simulator | None = None
    at: str
    ok: bool
    stage: str
    filtered: bool = False
    gl: bool = False  # a gate-level run_tests run (absent in ledgers written before M1-2)
    image: str
    image_id: str | None = None
    siliconsmith_version: str
    digests: Digests
    tests: list[RunTest] = Field(default_factory=list)
    synth: SynthFacts | None = None  # only on synthesize records


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


def _encode(ledger: Ledger) -> bytes:
    return (json.dumps(ledger.model_dump(mode="json"), separators=(",", ":")) + "\n").encode()


def _write(project_dir: Path, ledger: Ledger) -> None:
    """Write the ledger, dropping the oldest runs so the file always stays readable."""
    ledger.runs = ledger.runs[-MAX_RUNS:]
    data = _encode(ledger)
    while len(data) > WRITE_LEDGER_BYTES and ledger.runs:
        if len(ledger.runs) > 1:
            ledger.runs = ledger.runs[1:]
        else:
            ledger.runs[0].tests = []
        data = _encode(ledger)
    atomic_write(project_dir, LEDGER_REL, data, allow_hidden=True)


def _rescue_approval(raw: bytes) -> Approval | None:
    try:
        return Approval.model_validate(json.loads(raw)["approval"])
    except (ValueError, KeyError, TypeError):
        return None


def _load_for_update(project_dir: Path) -> Ledger:
    """The ledger to add to. A damaged file is moved aside first and a fresh ledger starts."""
    loaded = load_ledger(project_dir)
    if loaded.problem is None:
        return loaded.ledger
    fresh = Ledger()
    try:
        raw = safe_read(project_dir, LEDGER_REL, RESCUE_LEDGER_BYTES, allow_hidden=True)
    except ToolError:
        logger.warning("could not keep a copy of the damaged ledger; replacing it")
        return fresh
    fresh.approval = _rescue_approval(raw)  # the owner's approval survives a damaged run list
    if len(raw) <= WRITE_LEDGER_BYTES * 8:
        try:
            atomic_write(project_dir, CORRUPT_REL, raw, allow_hidden=True)
        except ToolError:
            logger.warning("could not keep a copy of the damaged ledger; replacing it")
    return fresh


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
        if hit or rel in ("test/tb.v", "test/Makefile"):
            wanted.append(rel)
    return wanted


def sim_inputs(project: Project, files: list[str]) -> list[str]:
    """The check inputs plus every cocotb test file (`test/**/*.py`, nested helpers too)."""
    tests = [r for r in files if r.startswith("test/") and r.endswith(".py")]
    return sorted({*check_inputs(project, files), *tests})


SYNTH_EXTRA_INPUTS = ("src/config.json", "info.yaml")


def synth_inputs(project: Project, files: list[str]) -> list[str]:
    """The check inputs plus `src/config.json` and `info.yaml`, which synthesis reads.

    The two extra files are always listed, so a missing one still changes the digest.
    """
    return sorted({*check_inputs(project, files), *SYNTH_EXTRA_INPUTS})


def _digest(project_dir: Path, rels: list[str], image: str) -> str:
    entries = [[rel, _file_hash(project_dir, rel)] for rel in sorted(rels)]
    blob = json.dumps({"files": entries, "image": image, "version": __version__}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def compute_digests(project: Project, image: str) -> Digests:
    """The check, sim, synth and spec digests of the project's current files."""
    files = [rel for rel, _ in walk_files(project.dir, skip_dirs=_SKIP_DIRS)]
    return Digests(
        check=_digest(project.dir, check_inputs(project, files), image),
        sim=_digest(project.dir, sim_inputs(project, files), image),
        synth=_digest(project.dir, synth_inputs(project, files), image),
        spec=spec_digest(project.dir),
    )


# -- recording a tool run ------------------------------------------------------------------


def snapshot_digests(project: Project, image: str) -> Digests | None:
    """Digests of the files as they are before a run starts, or None if they cannot be read.

    Taken before the run on purpose: a file edited while the run is going then shows as stale,
    never as tested. The caller holds the project lock.
    """
    try:
        return compute_digests(project, image)
    except Exception as exc:
        logger.warning("could not digest the project files: %s", exc)
        return None


def record_from_result(
    project: Project,
    image: str,
    tool: Tool,
    result: ToolResult,
    *,
    digests: Digests | None = None,
    image_id: str | None = None,
) -> RunRecord:
    """Turn a tool's result into a ledger record.

    `digests` should be the snapshot taken before the run; if None, the files are digested now.
    """
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
    synth: SynthFacts | None = None
    if tool == "synthesize" and isinstance(details.get("synth"), dict):
        try:
            synth = SynthFacts.model_validate(details["synth"])
        except ValidationError:
            synth = None  # facts only: drop what cannot be read rather than guess
    return RunRecord(
        tool=tool,
        sim=sim,
        at=now_utc(),
        ok=result.ok,
        stage=stage,
        filtered=bool(details.get("filtered", False)),
        gl=tool == "run_tests" and isinstance(details.get("gl"), dict),
        image=image,
        image_id=image_id,
        siliconsmith_version=__version__,
        digests=digests if digests is not None else compute_digests(project, image),
        tests=tests,
        synth=synth,
    )


def record_run(
    project: Project, image: str, tool: Tool, result: ToolResult, before: Digests | None
) -> ToolResult:
    """Append the run to the ledger and return `result`.

    `before` is `snapshot_digests` from before the run. If it is None the run is not recorded.

    The caller holds the project lock (it is not re-entrant, so this takes none). A ledger
    problem never turns a result into an error: it is logged and noted in the summary.
    """
    try:
        if before is None:
            raise ValueError("the project files could not be digested")
        append_run(project.dir, record_from_result(project, image, tool, result, digests=before))
    except Exception as exc:  # the ledger must never break a tool result
        logger.warning("could not update the validation ledger: %s", exc)
        lines = [*result.summary.splitlines(), "Note: the validation ledger could not be updated."]
        result.summary = make_summary(lines)
    return result
