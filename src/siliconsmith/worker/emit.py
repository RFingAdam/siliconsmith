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

import importlib.util
import inspect
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

from siliconsmith.worker import lint, pyerror
from siliconsmith.worker.wrapper_gen import TT_PORTS, check_core_ports, generate_wrapper

WORK = Path("/work")
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
MAX_CORE_BYTES = 1024 * 1024
MAX_FINDINGS = 200
TOOL_TIMEOUT_S = 120
SEED_CACHE = "/usr/local/bin/ss-seed-cache"

# Tiny Tapeout pins that MyHDL gets as 1-bit signals; the rest are 8 bits wide.
_BIT_PINS = ("clk", "ena")


class DesignError(Exception):
    """The design cannot be turned into a core; `file` is the project-relative file to fix."""

    def __init__(self, file: str, message: str) -> None:
        super().__init__(message)
        self.file = file


def _hdl_file(name: str) -> str:
    return f"hdl/{name}.py"


# -- step 1: elaboration -------------------------------------------------------------------


def _import_design(name: str) -> Any:
    """Import `/work/hdl/<name>.py` by path, with `/work/hdl` on sys.path for helper modules."""
    path = WORK / "hdl" / f"{name}.py"
    if not path.is_file():
        raise DesignError(_hdl_file(name), f"{_hdl_file(name)} does not exist")
    sys.path.insert(0, str(WORK / "hdl"))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DesignError(_hdl_file(name), f"cannot load {_hdl_file(name)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _elaborate_amaranth(name: str) -> str:
    # Warm Amaranth's bundled Yosys (WebAssembly) cache; it never fails the caller.
    subprocess.run([SEED_CACHE], check=False, capture_output=True, timeout=TOOL_TIMEOUT_S)
    from amaranth.back import verilog
    from amaranth.hdl import UnusedElaboratable
    from amaranth.lib import wiring

    warnings.simplefilter("ignore", UnusedElaboratable)
    module = _import_design(name)
    top = getattr(module, "top", None)
    if not callable(top):
        raise DesignError(
            _hdl_file(name),
            f"{_hdl_file(name)} must define a module-level function top() that returns an "
            "amaranth.lib.wiring.Component (see the starter file)",
        )
    design = top()
    if not isinstance(design, wiring.Component):
        raise DesignError(
            _hdl_file(name),
            f"top() in {_hdl_file(name)} returned {type(design).__name__}; it must return an "
            "amaranth.lib.wiring.Component whose ports are the Tiny Tapeout pins",
        )
    text: str = verilog.convert(design, name=name, emit_src=False)
    return text


def _elaborate_myhdl(name: str, run_dir: Path) -> str:
    from myhdl import ResetSignal, Signal, ToVerilogWarning, intbv

    module = _import_design(name)
    func = getattr(module, name, None)
    if not callable(func):
        raise DesignError(
            _hdl_file(name),
            f"{_hdl_file(name)} must define a function named {name} decorated with @block, "
            "whose parameters are Tiny Tapeout pin names",
        )
    params = list(inspect.signature(func).parameters)
    unknown = [p for p in params if p not in TT_PORTS]
    if unknown:
        raise DesignError(
            _hdl_file(name),
            f"parameter(s) {', '.join(unknown)} of {name}() are not Tiny Tapeout pins; "
            f"use only: {', '.join(TT_PORTS)}",
        )
    signals: dict[str, Any] = {}
    for param in params:
        if param == "rst_n":
            signals[param] = ResetSignal(0, active=0, isasync=False)
        elif param in _BIT_PINS:
            signals[param] = Signal(bool(0))
        else:
            signals[param] = Signal(intbv(0)[8:])
    instance = func(**signals)
    if not callable(getattr(instance, "convert", None)):
        raise DesignError(
            _hdl_file(name), f"{name}() in {_hdl_file(name)} must be decorated with @block"
        )
    target = run_dir / "myhdl"
    target.mkdir(parents=True, exist_ok=True)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ToVerilogWarning)
        instance.convert(
            hdl="Verilog",
            name=name,
            path=str(target),
            testbench=False,
            no_myhdl_header=True,
            timescale="1ns/1ps",
        )
    return (target / f"{name}.v").read_text(encoding="utf-8")


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
    out_dir: Path, name: str, *, generated: bool, core_only: bool = False
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
    name, frontend, author, run_dir = _check_request(request)
    out_dir = run_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = frontend != "verilog"
    entry = _hdl_file(name) if generated else f"src/{name}.v"

    # 1. the core
    try:
        if frontend == "amaranth":
            body = _elaborate_amaranth(name)
        elif frontend == "myhdl":
            body = _elaborate_myhdl(name, run_dir)
        else:
            body = _read_verilog_core(name)
    except DesignError as exc:
        return _result(out_dir, python_error=pyerror.design_error(exc.file, str(exc)))
    except BaseException as exc:  # the agent's code may raise anything, even SystemExit
        return _result(out_dir, python_error=pyerror.extract(exc, entry))
    text = (_core_header(author, name) + body) if generated else body
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
            message=f"the ports of {name} do not fit Tiny Tapeout:\n" + "\n".join(problems) + extra,
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
    found = _lint(out_dir, name, generated=generated)
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
