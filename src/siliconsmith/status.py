# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Derived validation status and the generated `docs/verification.md` (PLAN.md section 5.7).

`compute_status` works out the state of every step from the current project files and the
ledger's facts. It reads files but never runs anything, so it needs no Docker. Each step has its
own inputs (see `ledger.py` for the digests), so editing a file makes stale only the steps that
depend on it. The `status` tool and the `siliconsmith status` command both call `refresh_status`,
so they print the same table and write the same file.

The steps are data per track (`DIGITAL_STEPS`); the analog and acoustic tracks add their own
lists later.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith._version import __version__
from siliconsmith.covers import coverage
from siliconsmith.ledger import (
    Digests,
    Ledger,
    LedgerLoad,
    RunRecord,
    compute_digests,
    load_ledger,
)
from siliconsmith.paths import atomic_write, project_lock, safe_read
from siliconsmith.project import Project
from siliconsmith.spec import MAX_SPEC_BYTES, SPEC_NAME, Spec, parse_spec

DOC_REL = "docs/verification.md"
SIMULATORS = ("icarus", "verilator")
SIM_NAMES = {"icarus": "Icarus", "verilator": "Verilator"}
# Stages of the emit pipeline. A run that got to `build` or beyond had clean lint.
LINT_CLEAN_STAGES = frozenset({"done", "build", "sim"})
CHECK_STAGES = frozenset({"elaborate", "ports", "lint", *LINT_CLEAN_STAGES})
MAX_EVIDENCE = 90

State = Literal["pass", "fail", "stale", "not_run", "not_available"]


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    proves: str  # plain English, short enough for one table row
    milestone: str | None = None  # set for steps that are not built yet


DIGITAL_STEPS: tuple[Step, ...] = (
    Step("spec_valid", "Spec valid", "spec.yaml is complete and well formed"),
    Step("spec_approved", "Spec approved", "The owner agrees the spec says what they want"),
    Step("check", "Verilog check", "The design turns into Verilog that lints clean"),
    Step("sim_icarus", "Simulation: Icarus", "The tests pass on the Icarus simulator"),
    Step("sim_verilator", "Simulation: Verilator", "The tests pass on the Verilator simulator"),
    Step("coverage", "Requirement coverage", "Every requirement has a test that passes on both"),
    Step("synth_gl", "Synthesis + gate sim", "The design maps to real gates and still works", "M1"),
    Step("layout", "Layout signoff", "The layout fits the tile and passes the rules", "M2"),
    Step("tt_precheck", "Tiny Tapeout checks", "Tiny Tapeout's own checks accept it", "M3"),
    Step("bring_up", "Silicon bring-up", "The made chip works on the demo board", "M3"),
)


@dataclass(frozen=True)
class StepStatus:
    number: int
    key: str
    title: str
    proves: str
    state: State
    evidence: str
    date: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "key": self.key,
            "title": self.title,
            "proves": self.proves,
            "state": self.state,
            "evidence": self.evidence,
            "date": self.date,
        }


@dataclass(frozen=True)
class StatusReport:
    project: str
    steps: list[StepStatus]
    ledger_problem: str | None = None

    def table(self) -> str:
        return "\n".join(table_lines(self.steps))

    def summary_lines(self) -> list[str]:
        lines = table_lines(self.steps)
        if self.ledger_problem:
            lines.append(f"Note: {self.ledger_problem}; treating it as empty.")
        return lines


# -- helpers -------------------------------------------------------------------------------


def _clip(text: str, limit: int = MAX_EVIDENCE) -> str:
    text = " ".join(text.replace("|", "/").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _when(at: str | None) -> str | None:
    """`2026-11-12T14:03:05Z` -> `2026-11-12 14:03 UTC`."""
    if not at:
        return None
    return at[:16].replace("T", " ") + " UTC"


def _ranges(ids: list[str]) -> str:
    return ", ".join(ids) if len(ids) <= 4 else f"{ids[0]}..{ids[-1]}"


@dataclass(frozen=True)
class _Facts:
    project: Project
    image: str
    ledger: Ledger
    digests: Digests
    spec: Spec | None
    spec_errors: str | None  # why spec.yaml is not usable, or None


def _read_spec(project: Project) -> tuple[Spec | None, str | None]:
    try:
        text = safe_read(project.dir, SPEC_NAME, MAX_SPEC_BYTES).decode("utf-8")
    except UnicodeDecodeError:
        return None, "spec.yaml is not valid UTF-8 text"
    except ToolError:
        return None, "spec.yaml was not found or cannot be read"
    parsed = parse_spec(text)
    if isinstance(parsed, Spec):
        return parsed, None
    first = parsed[0]
    more = f" (+{len(parsed) - 1} more)" if len(parsed) > 1 else ""
    return None, f"{first.path}: {first.message}{more}"


def _latest(runs: list[RunRecord], keep: Callable[[RunRecord], bool]) -> RunRecord | None:
    for run in reversed(runs):
        if keep(run):
            return run
    return None


def _ran(run: RunRecord) -> list[Any]:
    return [t for t in run.tests if t.outcome != "skip"]


def _latest_full(runs: list[RunRecord], sim: str) -> RunRecord | None:
    """The latest unfiltered run_tests run on `sim` in which at least one test ran."""
    return _latest(
        runs,
        lambda r: r.tool == "run_tests" and r.sim == sim and not r.filtered and bool(_ran(r)),
    )


# -- the steps -----------------------------------------------------------------------------


def _spec_valid(f: _Facts) -> tuple[State, str, str | None]:
    if f.spec is not None:
        ids = f.spec.requirement_ids()
        return "pass", f"{len(ids)} requirement(s): {_ranges(ids)}", None
    missing = f.spec_errors is not None and "not found" in f.spec_errors
    return ("not_run" if missing else "fail"), _clip(f.spec_errors or "spec.yaml is invalid"), None


def _spec_approved(f: _Facts) -> tuple[State, str, str | None]:
    approval = f.ledger.approval
    if approval is None:
        return "not_run", "Not approved yet: the owner runs `siliconsmith approve <dir>`", None
    who, when = approval.approved_by, _when(approval.at)
    if approval.spec_sha256 == f.digests.spec:
        return "pass", _clip(f"Approved by {who}"), when
    return "stale", _clip(f"spec.yaml changed since {who} approved it"), when


def _check(f: _Facts) -> tuple[State, str, str | None]:
    run = _latest(f.ledger.runs, lambda r: r.stage in CHECK_STAGES)
    if run is None:
        return "not_run", "Call emit_verilog to generate and lint the Verilog", None
    when = _when(run.at)
    name = f"{run.tool} {run.sim}" if run.sim else run.tool
    if run.digests.check != f.digests.check:
        return "stale", _clip(f"Design files changed since {name} last ran"), when
    if run.stage in LINT_CLEAN_STAGES:
        return "pass", f"{name}: Verilog generated and lint clean", when
    return "fail", f"{name} failed at the {run.stage} stage", when


def _sim_step(sim: str) -> Callable[[_Facts], tuple[State, str, str | None]]:
    def step(f: _Facts) -> tuple[State, str, str | None]:
        run = _latest_full(f.ledger.runs, sim)
        if run is None:
            return "not_run", f"Call run_tests on {sim} without a test_filter", None
        when = _when(run.at)
        if run.digests.sim != f.digests.sim:
            return "stale", _clip(f"Design or test files changed since run_tests {sim}"), when
        ran = _ran(run)
        passed = [t for t in ran if t.outcome == "pass"]
        if run.ok:
            return "pass", f"run_tests {sim}: {len(passed)}/{len(ran)} passed", when
        failed = [t.name for t in ran if t.outcome != "pass"]
        return (
            "fail",
            _clip(
                f"run_tests {sim}: {len(passed)}/{len(ran)} passed; failed: " + ", ".join(failed)
            ),
            when,
        )

    return step


def _coverage(f: _Facts) -> tuple[State, str, str | None]:
    if f.spec is None:
        return "not_run", "Needs a valid spec.yaml", None
    runs: dict[str, RunRecord] = {}
    for sim in SIMULATORS:
        run = _latest_full(f.ledger.runs, sim)
        if run is not None:
            runs[sim] = run
    missing = [SIM_NAMES[s] for s in SIMULATORS if s not in runs]
    if missing:
        return "not_run", "Needs a full run_tests on " + " and ".join(missing), None
    when = _when(max(r.at for r in runs.values()))
    if any(
        r.digests.sim != f.digests.sim or r.digests.spec != f.digests.spec for r in runs.values()
    ):
        return "stale", "Spec, design or test files changed since the last full runs", when
    broken = [SIM_NAMES[s] for s, r in runs.items() if not r.ok]
    if broken:
        return "fail", "The latest full run failed on " + " and ".join(broken), when
    ids = f.spec.requirement_ids()
    covered_on = [
        set(coverage(ids, [t.model_dump() for t in r.tests]).covered) for r in runs.values()
    ]
    both = covered_on[0] & covered_on[1]
    uncovered = [i for i in ids if i not in both]
    if uncovered:
        return "fail", _clip(f"{_ranges(uncovered)} not covered by a test passing on both"), when
    return "pass", f"All {len(ids)} requirement(s) covered on both simulators", when


Evaluator = Callable[[_Facts], tuple[State, str, str | None]]

EVALUATORS: dict[str, Evaluator] = {
    "spec_valid": _spec_valid,
    "spec_approved": _spec_approved,
    "check": _check,
    "sim_icarus": _sim_step("icarus"),
    "sim_verilator": _sim_step("verilator"),
    "coverage": _coverage,
}


def compute_status(
    project: Project, image: str, steps: tuple[Step, ...] = DIGITAL_STEPS
) -> StatusReport:
    """Work out every step's state. The caller holds the project lock (or accepts a race)."""
    loaded: LedgerLoad = load_ledger(project.dir)
    spec, spec_errors = _read_spec(project)
    facts = _Facts(
        project, image, loaded.ledger, compute_digests(project, image), spec, spec_errors
    )
    out: list[StepStatus] = []
    for number, step in enumerate(steps, start=1):
        if step.milestone is not None:
            state: State = "not_available"
            evidence, date = f"Comes with milestone {step.milestone}", None
        else:
            state, evidence, date = EVALUATORS[step.key](facts)
        out.append(StepStatus(number, step.key, step.title, step.proves, state, evidence, date))
    return StatusReport(project.name, out, loaded.problem)


# -- rendering -----------------------------------------------------------------------------


def _state_text(state: State) -> str:
    return state.replace("_", " ")


def table_lines(steps: list[StepStatus]) -> list[str]:
    lines = [
        "| Step | What it proves | Status | Evidence | Date |",
        "|---|---|---|---|---|",
    ]
    for s in steps:
        lines.append(
            f"| {s.number}. {s.title} | {s.proves} | {_state_text(s.state)} | "
            f"{_clip(s.evidence)} | {s.date or '-'} |"
        )
    return lines


def render_document(report: StatusReport) -> str:
    """The text of `docs/verification.md`. It has no clock reading, so it is reproducible."""
    parts = [
        "<!-- Generated by Siliconsmith. Do not edit: changes are overwritten. -->",
        "",
        f"# Verification status: {report.project}",
        "",
        f"This file is generated (Siliconsmith {__version__}). Refresh it with the `status` tool "
        "or `siliconsmith status <project folder>`. It shows which checks have passed for the "
        "files as they are now.",
        "",
        *table_lines(report.steps),
        "",
        "How to read the Status column:",
        "",
        "- **pass**: this check passed on the current files.",
        "- **fail**: this check ran on the current files and failed.",
        "- **stale**: it passed or failed before, but a file it depends on has changed since. "
        "Run it again.",
        "- **not run**: nothing has been recorded for this step yet.",
        "- **not available**: the step belongs to a later milestone of Siliconsmith.",
        "",
        "Approval is the owner's step. Nothing an AI agent does can approve the spec: the owner "
        "reads spec.yaml and runs `siliconsmith approve <project folder>` in a terminal. This is "
        "an honest-use convention, not a security boundary.",
    ]
    if report.ledger_problem:
        parts += ["", f"Note: {report.ledger_problem}; it was treated as empty."]
    return "\n".join(parts) + "\n"


def refresh_status(project: Project, image: str) -> StatusReport:
    """Compute the status and write `docs/verification.md`, under the project lock."""
    with project_lock(project.dir):
        report = compute_status(project, image)
        atomic_write(project.dir, DOC_REL, render_document(report).encode("utf-8"))
    return report


__all__ = [
    "DIGITAL_STEPS",
    "DOC_REL",
    "StatusReport",
    "StepStatus",
    "compute_status",
    "refresh_status",
    "render_document",
]
