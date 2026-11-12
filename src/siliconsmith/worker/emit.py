# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The `emit` verb: design source -> Verilog core -> checked ports -> wrapper -> lint.

One container call does the whole pipeline, so `run_tests` can reuse it. The steps:

1. Elaborate the core: Amaranth or MyHDL source is imported and converted to Verilog (this runs
   the agent's Python, which is why it happens in the container); a Verilog core is read as is.
2. Read the core's ports with Yosys and check them against the Tiny Tapeout pin set.
3. Generate the `tt_um_<name>` wrapper (same generator as the host) and read its ports back.
4. Lint core and wrapper with Verilator.

Everything is written to `<run_dir>/out`: `<name>.v` and `project.v`. The result says how far the
pipeline got (`stage`), and every message uses project-relative paths. Stdlib only at module
level; amaranth and myhdl are imported inside the functions that need them.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from siliconsmith.worker import lint, pyerror
from siliconsmith.worker.elaborate import DesignError, hdl_file
from siliconsmith.worker.limits import MAX_FINDINGS, MAX_GENERATED_BYTES, clamp_result
from siliconsmith.worker.wrapper_gen import (
    TT_PORTS,
    check_core_ports,
    describe_port_problems,
    generate_wrapper,
)

WORK = Path("/work")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
MAX_CORE_BYTES = 1024 * 1024
TOOL_TIMEOUT_S = 120
ELABORATE_TIMEOUT_S = 200
SEED_CACHE = "/usr/local/bin/ss-seed-cache"

_hdl_file = hdl_file


# -- step 1: elaboration -------------------------------------------------------------------


def _reap_processes() -> None:
    """Kill every process the design left behind (all but this one and PID 1).

    Design code runs as this user in this container, so a process it forks could outlive the
    elaboration and rewrite files, or the result, while Yosys and Verilator run. `kill(-1)`
    does not signal the caller. Only done inside the container: on a developer machine this
    would kill the user's own processes.
    """
    if not in_sandbox():
        return
    with contextlib.suppress(OSError):
        os.kill(-1, signal.SIGKILL)


def _read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(1 << 20)
    except OSError:
        return ""


def in_sandbox(proc: str = "/proc") -> bool:
    """True only inside the Siliconsmith container, never on a developer machine.

    All of these must hold: `/work` is the work folder, PID 1 is Docker's `--init` process
    (`docker-init`, which is tini), and the root filesystem is mounted read-only (`--read-only`).
    A developer shell or a dev container fails at least one of them.
    """
    if Path("/work") != WORK:
        return False
    if _read_text(f"{proc}/1/comm").strip() not in {"docker-init", "tini"}:
        return False
    for line in _read_text(f"{proc}/self/mounts").splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "/":
            return "ro" in fields[3].split(",")
    return False


def _elaborate(name: str, frontend: str, run_dir: Path) -> tuple[str | None, dict[str, Any] | None]:
    """Run the design in a child process: `(Verilog text, None)` or `(None, python_error)`.

    A toolchain failure (a library that does not import, no seed-cache helper) raises
    RuntimeError, which the worker's `main` reports as an infrastructure error.
    """
    entry = _hdl_file(name)
    if not (WORK / "hdl" / f"{name}.py").is_file():
        return None, pyerror.design_error(entry, f"{entry} does not exist")
    if frontend == "amaranth":
        # Warm Amaranth's bundled Yosys (WebAssembly) cache; best effort, never the design's fault.
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run([SEED_CACHE], check=False, capture_output=True, timeout=TOOL_TIMEOUT_S)
    elab = run_dir / "elab.json"
    elab.unlink(missing_ok=True)
    payload = json.dumps({"frontend": frontend, "name": name, "run_dir": str(run_dir)})
    exit_code: int | None = None
    stderr = ""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "siliconsmith.worker.elaborate"],
            input=payload,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=ELABORATE_TIMEOUT_S,
            check=False,
        )
        exit_code, stderr = proc.returncode, proc.stderr
    except subprocess.TimeoutExpired:
        _reap_processes()
        return None, pyerror.design_error(
            entry, f"elaboration of {entry} did not finish in {ELABORATE_TIMEOUT_S} s"
        )
    _reap_processes()
    try:
        outcome = json.loads(elab.read_text(encoding="utf-8"))
        if not isinstance(outcome, dict):
            raise ValueError("not an object")
    except (OSError, ValueError):
        tail = _tool_tail(stderr, name, run_dir / "out", lines=3)
        return None, pyerror.design_error(
            entry,
            f"{entry} stopped the Python interpreter before it produced Verilog "
            f"(exit code {exit_code})" + (f":\n{tail}" if tail else ""),
        )
    if isinstance(outcome.get("internal"), str):
        raise RuntimeError(outcome["internal"])
    error = outcome.get("design_error")
    if isinstance(error, dict):
        return None, pyerror.design_error(str(error.get("file", entry)), str(error.get("message")))
    python_error = outcome.get("python_error")
    if isinstance(python_error, dict):
        return None, python_error
    body = outcome.get("body")
    if not isinstance(body, str):
        return None, pyerror.design_error(entry, f"{entry} produced no Verilog")
    return body, None


def _read_verilog_core(name: str) -> str:
    path = WORK / "src" / f"{name}.v"
    if not path.is_file():
        raise DesignError(f"src/{name}.v", f"src/{name}.v does not exist")
    if path.stat().st_size > MAX_CORE_BYTES:
        raise DesignError(f"src/{name}.v", f"src/{name}.v is larger than {MAX_CORE_BYTES} bytes")
    return path.read_text(encoding="utf-8", errors="replace")


def _core_header(author: str, name: str) -> str:
    return (
        f"// SPDX-FileCopyrightText: {author}\n"
        # REUSE-IgnoreStart
        "// SPDX-License-Identifier: Apache-2.0\n"
        # REUSE-IgnoreEnd
        f"// Generated by siliconsmith from {_hdl_file(name)}. Do not edit.\n\n"
    )


# -- step 2 and 3: ports through Yosys -----------------------------------------------------


def _out_paths(text: str, name: str, out_dir: Path) -> str:
    """Show files in `out_dir` as the project files they stand for, and drop `/work/`."""
    text = text.replace(f"{out_dir}/{name}.v", f"src/{name}.v")
    text = text.replace(f"{out_dir}/project.v", "src/project.v")
    return pyerror.map_paths(text)


def _project_files(text: str, name: str) -> str:
    """`counter.v:3:25` (Verilator's short names) -> `src/counter.v:3:25`."""
    return re.sub(rf"(?<![\w/.])({re.escape(name)}|project)\.v:", r"src/\1.v:", text)


def _tool_tail(text: str, name: str, out_dir: Path, lines: int = 8) -> str:
    kept = [ln for ln in _out_paths(text, name, out_dir).splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _yosys_ports(
    out_dir: Path, name: str, sources: list[str], top: str, json_name: str
) -> tuple[dict[str, tuple[str, int]] | None, str]:
    """Ports of module `top` as `{name: (direction, width)}`, or `(None, error text)`."""
    script = (
        f"read_verilog -sv {' '.join(sources)}; hierarchy -top {top}; proc; write_json {json_name}"
    )
    try:
        proc = subprocess.run(
            ["yosys", "-q", "-p", script],
            cwd=out_dir,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"yosys did not finish within {TOOL_TIMEOUT_S} s"
    if proc.returncode != 0:
        return None, _tool_tail(proc.stdout + "\n" + proc.stderr, name, out_dir)
    try:
        modules = json.loads((out_dir / json_name).read_text(encoding="utf-8"))["modules"]
        raw = modules[top]["ports"]
        return {p: (str(v["direction"]), len(v["bits"])) for p, v in raw.items()}, ""
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, f"cannot read the ports from yosys output ({type(exc).__name__}: {exc})"


def _ports_json(ports: dict[str, tuple[str, int]]) -> dict[str, dict[str, Any]]:
    order = {p: i for i, p in enumerate(TT_PORTS)}
    ordered = sorted(ports, key=lambda p: (order.get(p, len(order)), p))
    return {p: {"direction": ports[p][0], "width": ports[p][1]} for p in ordered}


# -- step 4: lint --------------------------------------------------------------------------


def _lint(
    out_dir: Path,
    name: str,
    *,
    generated: bool,
    core_only: bool = False,
    width_checks: bool = True,
) -> list[dict[str, Any]]:
    """Run Verilator over the wrapper and the core (or over the core alone)."""
    command = [
        "verilator",
        "--lint-only",
        "-Wall",
        "--timing",
        "--timescale",
        "1ns/1ps",
        "-Werror-LATCH",
        "-Werror-MULTIDRIVEN",
        "-Wno-DECLFILENAME",
    ]
    if generated:
        # Generated cores list every pin the design declares, so unused bits are the design's
        # business. Amaranth gives every register an initial value, which Verilator reports as
        # PROCASSINIT. Neither is something the agent can fix in the generated text.
        command += ["-Wno-UNUSEDSIGNAL", "-Wno-PROCASSINIT"]
    if not width_checks:
        # Amaranth defines every width itself, and its Verilog writer emits constants at their
        # smallest width, so `x == 0` on a 3-bit x reads as a width mismatch in the generated
        # text. The agent cannot fix that in its Amaranth source, so skip those two warnings.
        command += ["-Wno-WIDTHEXPAND", "-Wno-WIDTHTRUNC"]
    if core_only:
        command.append(f"{name}.v")
    else:
        command += ["--top-module", f"tt_um_{name}", "project.v", f"{name}.v"]
    try:
        proc = subprocess.run(
            command,
            cwd=out_dir,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [_finding("error", "TIMEOUT", f"verilator did not finish within {TOOL_TIMEOUT_S} s")]
    found = lint.parse(proc.stderr + "\n" + proc.stdout)
    if proc.returncode != 0 and not found:
        found = [_finding("error", "VERILATOR", _tool_tail(proc.stderr, name, out_dir) or "failed")]
    for item in found:
        item["file"] = _lint_file(item["file"], name, out_dir)
        for key in ("msg", "detail"):
            item[key] = _project_files(_out_paths(item[key], name, out_dir), name)
    return found


def _yosys_check(out_dir: Path, name: str) -> list[dict[str, Any]]:
    """What Verilator does not catch: multiple drivers (except across clocking), logic loops."""
    script = f"read_verilog -sv {name}.v; hierarchy -top {name}; proc; check -assert"
    try:
        proc = subprocess.run(
            ["yosys", "-q", "-p", script],
            cwd=out_dir,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [_finding("error", "TIMEOUT", f"yosys check did not finish in {TOOL_TIMEOUT_S} s")]
    if proc.returncode == 0:
        return []
    output = proc.stdout + "\n" + proc.stderr
    found = lint.parse_yosys_check(output, f"src/{name}.v")
    for item in found:
        item["file"] = _lint_file(item["file"], name, out_dir)
        item["msg"] = _project_files(_out_paths(item["msg"], name, out_dir), name)
    return found or [
        _finding("error", "YOSYS_CHECK", _tool_tail(output, name, out_dir) or "failed")
    ]


def _finding(severity: str, code: str, msg: str) -> dict[str, Any]:
    return {"severity": severity, "code": code, "file": "", "line": 0, "col": 0, "msg": msg,
            "detail": ""}  # fmt: skip


def _lint_file(path: str, name: str, out_dir: Path) -> str:
    """`project.v` / `<name>.v` as Verilator printed them -> `src/project.v` / `src/<name>.v`."""
    if path.startswith(f"{out_dir}/"):
        path = path[len(str(out_dir)) + 1 :]
    return f"src/{path}" if path in ("project.v", f"{name}.v") else pyerror.map_paths(path)


# -- the pipeline --------------------------------------------------------------------------


def _result(out_dir: Path, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": False,
        "stage": "elaborate",
        "run_id": out_dir.parent.name,
        "ports": None,
        "wrapper_ports": None,
        "lint": [],
        "lint_total": 0,
        "python_error": None,
        "message": None,
        "core_generated": False,
    }
    base.update(fields)
    return base


def _check_request(request: dict[str, Any]) -> tuple[str, str, str, Path]:
    name, frontend, author = request.get("name"), request.get("frontend"), request.get("author")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError(f"bad project name {name!r}")
    if frontend not in ("amaranth", "myhdl", "verilog"):
        raise ValueError(f"bad frontend {frontend!r}")
    if not isinstance(author, str) or not author.strip() or "\n" in author or "\r" in author:
        raise ValueError("author must be one line of text")
    run_dir = request.get("run_dir")
    if not isinstance(run_dir, str) or not run_dir.startswith(f"{WORK}/build/"):
        raise ValueError(f"run_dir must be a folder under {WORK}/build")
    return name, frontend, author, Path(run_dir)


def emit(request: dict[str, Any]) -> dict[str, Any]:
    """Run the pipeline; every text field in the result is clipped to the host's limits."""
    return clamp_result(_emit(request))


def _emit(request: dict[str, Any]) -> dict[str, Any]:
    name, frontend, author, run_dir = _check_request(request)
    out_dir = run_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = frontend != "verilog"
    entry = _hdl_file(name) if generated else f"src/{name}.v"

    # 1. the core
    try:
        if frontend == "verilog":
            body: str | None = _read_verilog_core(name)
        else:
            body, python_error = _elaborate(name, frontend, run_dir)
            if body is None:
                return _result(out_dir, python_error=python_error)
    except DesignError as exc:
        return _result(out_dir, python_error=pyerror.design_error(exc.file, str(exc)))
    assert body is not None
    text = (_core_header(author, name) + body) if generated else body
    if generated and len(text.encode("utf-8")) > MAX_GENERATED_BYTES:
        return _result(
            out_dir,
            python_error=pyerror.design_error(
                entry,
                f"the Verilog generated from {entry} is larger than {MAX_GENERATED_BYTES} bytes",
            ),
        )
    (out_dir / f"{name}.v").write_text(text, encoding="utf-8")

    # 2. the core's ports
    ports, error = _yosys_ports(out_dir, name, [f"{name}.v"], name, "core.json")
    if ports is None:
        found = [] if generated else _lint(out_dir, name, generated=False, core_only=True)
        syntax_errors = [f for f in found if f["severity"] == "error"]
        if (
            syntax_errors
        ):  # a Verilog syntax error reads better as a lint finding than as Yosys text
            return _result(
                out_dir,
                stage="lint",
                lint=syntax_errors[:MAX_FINDINGS],
                lint_total=len(syntax_errors),
            )
        return _result(
            out_dir,
            stage="ports",
            message=f"yosys cannot read the core module {name!r} from {entry}:\n{error}",
        )
    extra = ""
    if "rst" in ports and "rst" not in TT_PORTS and frontend == "amaranth":
        extra = (
            "\nAn Amaranth design that does not create its own sync clock domain gets an "
            "extra 'rst' port. Create ClockDomain('sync') and drive it from clk and ~rst_n, "
            "as the starter file does."
        )
    problems = check_core_ports(ports)
    if problems:
        return _result(
            out_dir,
            stage="ports",
            ports=_ports_json(ports),
            message=f"the ports of {name} do not fit Tiny Tapeout:\n"
            + describe_port_problems(problems)
            + extra,
        )

    # 3. the wrapper
    directions = {p: d for p, (d, _) in ports.items()}
    (out_dir / "project.v").write_text(generate_wrapper(name, directions, author), encoding="utf-8")
    wrapper_ports, error = _yosys_ports(
        out_dir, name, ["project.v", f"{name}.v"], f"tt_um_{name}", "wrapper.json"
    )
    expected = {p: (d, w) for p, (d, w) in TT_PORTS.items()}
    if wrapper_ports != expected:
        detail = error or "the ports differ from the Tiny Tapeout pin set"
        return _result(
            out_dir,
            stage="ports",
            ports=_ports_json(ports),
            message=f"the generated wrapper tt_um_{name} is not valid: {detail}",
        )

    # 4. lint
    found = _lint(out_dir, name, generated=generated, width_checks=frontend != "amaranth")
    found += _yosys_check(out_dir, name)
    return _result(
        out_dir,
        ok=not found,
        stage="done" if not found else "lint",
        ports=_ports_json(ports),
        wrapper_ports=_ports_json(wrapper_ports),
        lint=found[:MAX_FINDINGS],
        lint_total=len(found),
        core_generated=generated,
    )
