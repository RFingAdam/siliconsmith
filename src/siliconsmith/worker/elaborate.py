# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Under section 7 of the GNU AGPL version 3, you are granted the additional
# permission in OUTPUT-EXCEPTION.md: files this module writes into a user's
# project, and the text it copies into them, may be used under Apache-2.0.

"""Elaborate the agent's Amaranth or MyHDL design into Verilog text, in a child process.

This is the only worker code that imports and runs the agent's Python. `emit` starts it as
`python -m siliconsmith.worker.elaborate` and kills every process it left behind before it runs
Yosys and Verilator or writes the result, so design code cannot reach the lint verdict (it can
only change the Verilog text, which is then checked like any other text).

The request is JSON on stdin (`frontend`, `name`, `run_dir`). The outcome is one JSON file,
`<run_dir>/elab.json`, with exactly one of:

* `{"body": <Verilog text>}`
* `{"design_error": {"file": ..., "message": ...}}` (no `top()`, wrong parameters, ...)
* `{"python_error": {...}}` (the design raised; same shape as `pyerror.extract`)
* `{"internal": <text>}` (the toolchain, not the design, failed: a library did not import)

Stdlib only at module level; amaranth and myhdl are imported inside the functions.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import warnings
from pathlib import Path
from typing import Any

from siliconsmith.worker import pyerror
from siliconsmith.worker.wrapper_gen import TT_PORTS

WORK = Path("/work")
# Tiny Tapeout pins that MyHDL gets as 1-bit signals; the rest are 8 bits wide.
_BIT_PINS = ("clk", "ena")


class DesignError(Exception):
    """The design cannot be turned into a core; `file` is the project-relative file to fix."""

    def __init__(self, file: str, message: str) -> None:
        super().__init__(message)
        self.file = file


def hdl_file(name: str) -> str:
    return f"hdl/{name}.py"


def _import_design(name: str) -> Any:
    """Import `/work/hdl/<name>.py` by path, with `/work/hdl` on sys.path for helper modules."""
    path = WORK / "hdl" / f"{name}.py"
    if not path.is_file():
        raise DesignError(hdl_file(name), f"{hdl_file(name)} does not exist")
    sys.path.insert(0, str(WORK / "hdl"))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise DesignError(hdl_file(name), f"cannot load {hdl_file(name)}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _elaborate_amaranth(name: str) -> str:
    from amaranth.back import verilog
    from amaranth.hdl import UnusedElaboratable
    from amaranth.lib import wiring

    warnings.simplefilter("ignore", UnusedElaboratable)
    module = _import_design(name)
    top = getattr(module, "top", None)
    if not callable(top):
        raise DesignError(
            hdl_file(name),
            f"{hdl_file(name)} must define a module-level function top() that returns an "
            "amaranth.lib.wiring.Component (see the starter file)",
        )
    design = top()
    if not isinstance(design, wiring.Component):
        raise DesignError(
            hdl_file(name),
            f"top() in {hdl_file(name)} returned {type(design).__name__}; it must return an "
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
            hdl_file(name),
            f"{hdl_file(name)} must define a function named {name} decorated with @block, "
            "whose parameters are Tiny Tapeout pin names",
        )
    params = list(inspect.signature(func).parameters)
    unknown = [p for p in params if p not in TT_PORTS]
    if unknown:
        raise DesignError(
            hdl_file(name),
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
            hdl_file(name), f"{name}() in {hdl_file(name)} must be decorated with @block"
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


def _check_libraries(frontend: str) -> None:
    """Import the toolchain libraries before any design code runs; a failure here is internal."""
    if frontend == "amaranth":
        from amaranth.back import verilog  # noqa: F401
        from amaranth.hdl import UnusedElaboratable  # noqa: F401
        from amaranth.lib import wiring  # noqa: F401
    else:
        from myhdl import ResetSignal, Signal, ToVerilogWarning, intbv  # noqa: F401


def _write(run_dir: Path, outcome: dict[str, Any]) -> None:
    target = run_dir / "elab.json"
    tmp = run_dir / ".elab.json.tmp"
    tmp.write_text(json.dumps(outcome), encoding="utf-8")
    tmp.replace(target)


def run(request: dict[str, Any]) -> dict[str, Any]:
    """Elaborate and return the outcome dict (see the module docstring)."""
    frontend, name, run_dir = request["frontend"], request["name"], Path(request["run_dir"])
    try:
        _check_libraries(frontend)
    except Exception as exc:
        return {"internal": f"{type(exc).__name__}: {exc}"}
    entry = hdl_file(name)
    try:
        if frontend == "amaranth":
            return {"body": _elaborate_amaranth(name)}
        return {"body": _elaborate_myhdl(name, run_dir)}
    except DesignError as exc:
        return {"design_error": {"file": exc.file, "message": str(exc)}}
    except BaseException as exc:  # the agent's code may raise anything, even SystemExit
        return {"python_error": pyerror.extract(exc, entry)}


def main() -> int:
    request = json.loads(sys.stdin.read())
    outcome = run(request)
    _write(Path(request["run_dir"]), outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
