# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The `test` verb: emit (re-run in full), then simulate with cocotb on Icarus or Verilator.

One container call. The emit pipeline in `worker/emit.py` runs first and its result is returned
as it is; only when it is ok does the simulation start. The simulation uses cocotb's Python
runner (`cocotb_tools.runner`) with the Tiny Tapeout testbench `test/tb.v` as top level (`tb`),
the freshly emitted `out/project.v` and `out/<name>.v` as sources, 1 ns / 1 ps timescale and,
for Verilator, `--timing`.

The agent's test modules run inside the simulator process, a child of this one, with the project
`test/` folder first on its Python path. Simulator output goes to log files under the run
folder. Build folders live in `build/sim/` (and ccache in `build/.cache`), so a second run only
rebuilds what changed. With `waves`, the wave file is copied to `build/waves/<simulator>/`.

`cocotb_tools` is imported inside the function that needs it; everything else is stdlib.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from siliconsmith.worker import emit, pyerror, results
from siliconsmith.worker import waves as wavefile
from siliconsmith.worker.limits import (
    LOG_LINE_MAX,
    MAX_LOG_LINES,
    WAVES_PATH_MAX,
    clip,
)

WORK = emit.WORK
SIMULATORS = ("icarus", "verilator")
MODULE_RE = re.compile(r"^test[A-Za-z0-9_]*$")
MAX_FILTER_CHARS = 200
MAX_WAVE_BYTES = 256 * 1024 * 1024
WAVE_EXTENSIONS = {"fst": ".fst", "vcd": ".vcd"}
_ERROR_LINE = re.compile(r"error|fatal|cannot|not found|undefined|no such", re.IGNORECASE)


def _check(request: dict[str, Any]) -> tuple[str, str | None, bool]:
    simulator = request.get("simulator")
    if simulator not in SIMULATORS:
        raise ValueError(f"bad simulator {simulator!r}")
    test_filter = request.get("test_filter")
    if test_filter is not None:
        if not isinstance(test_filter, str) or len(test_filter) > MAX_FILTER_CHARS:
            raise ValueError("test_filter must be a short string")
        try:
            re.compile(test_filter)
        except re.error as exc:
            raise ValueError(f"test_filter is not a valid regular expression: {exc}") from exc
    waves = request.get("waves", False)
    if not isinstance(waves, bool):
        raise ValueError("waves must be true or false")
    return str(simulator), test_filter, waves


def discover_modules(test_dir: Path) -> list[str]:
    """Names of the `test*.py` modules directly in `test_dir`, sorted."""
    found = []
    for path in sorted(test_dir.glob("test*.py")):
        if path.is_file() and not path.is_symlink() and MODULE_RE.fullmatch(path.stem):
            found.append(path.stem)
    return found


def _read_text(path: Path, limit: int = 1 << 20) -> str:
    try:
        with open(path, "rb") as handle:
            data = handle.read(limit)
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def map_log_paths(text: str, out_dir: Path, name: str) -> str:
    """Show simulator paths as project paths: `out/<name>.v` -> `src/<name>.v`, `/work/` dropped."""
    text = text.replace(f"{out_dir}/{name}.v", f"src/{name}.v")
    text = text.replace(f"{out_dir}/project.v", "src/project.v")
    return pyerror.map_paths(text)


def log_lines(text: str, out_dir: Path, name: str) -> list[str]:
    """The lines worth showing for a failed build or a crashed simulation (clipped, at most 15).

    Lines that look like errors come first; if there are none, the last lines of the log.
    """
    mapped = [ln.rstrip() for ln in map_log_paths(text, out_dir, name).splitlines() if ln.strip()]
    errors = [ln for ln in mapped if _ERROR_LINE.search(ln)]
    chosen = errors[:MAX_LOG_LINES] if errors else mapped[-MAX_LOG_LINES:]
    return [clip(ln, LOG_LINE_MAX) for ln in chosen]


def _outcome(state: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "state": state,
        "simulator": None,
        "tests": [],
        "tests_total": 0,
        "waves": None,
        "log": [],
        "exit_code": None,
        "message": None,
    }
    base.update(fields)
    return base


def _is_wave_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink() and path.stat().st_size > 0
    except OSError:
        return False


def find_wave_file(simulator: str, sim_dir: Path, build_dir: Path) -> Path | None:
    """The wave file a run left behind. Icarus writes `tb.fst` (cwd or build dir), Verilator
    writes `dump.fst` or `dump.vcd` into the folder it ran in."""
    names = {
        "icarus": ("tb.fst", "tb.vcd"),
        # `tb.fst` comes from the testbench's own $dumpfile; cocotb's `dump.*` is a second, much
        # emptier trace (Verilator under --timing), so it is only a fallback.
        "verilator": ("tb.fst", "tb.vcd", "dump.fst", "dump.vcd"),
    }[simulator]
    for folder in (sim_dir, build_dir):
        for file_name in names:
            candidate = folder / file_name
            if _is_wave_file(candidate):
                return candidate
    return None


def _mkdir_nofollow(base: Path, *parts: str) -> Path:
    """Create `base/parts...` one folder at a time, refusing a symlink on the way."""
    current = base
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise OSError(f"{current} is a symbolic link")
        current.mkdir(exist_ok=True)
        if current.is_symlink() or not current.is_dir():
            raise OSError(f"{current} is not a folder")
    return current


def keep_waves(simulator: str, source: Path) -> tuple[str | None, str | None]:
    """Copy the wave file to `build/waves/<simulator>/tb.<fst|vcd>`: `(project path, note)`."""
    fmt = wavefile.sniff_format(source)
    if fmt is None:
        return None, f"the wave file {source.name} is neither VCD nor FST"
    if source.stat().st_size > MAX_WAVE_BYTES:
        return None, f"the wave file is larger than {MAX_WAVE_BYTES >> 20} MiB and was not kept"
    try:
        folder = _mkdir_nofollow(WORK / "build", "waves", simulator)
        for old in folder.iterdir():
            if old.is_file() or old.is_symlink():
                old.unlink()
        target = folder / f"tb{WAVE_EXTENSIONS[fmt]}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        with open(source, "rb") as src, os.fdopen(os.open(target, flags, 0o644), "wb") as dst:
            shutil.copyfileobj(src, dst)
    except OSError as exc:
        return None, f"cannot keep the wave file ({exc.__class__.__name__}: {exc})"
    return f"build/waves/{simulator}/tb{WAVE_EXTENSIONS[fmt]}", None


def _prepare_dirs(simulator: str, waves: bool, run_dir: Path) -> tuple[Path, Path]:
    sim_dir = run_dir / "sim"
    sim_dir.mkdir(parents=True, exist_ok=True)
    build_dir = _mkdir_nofollow(WORK / "build", "sim", simulator + ("-waves" if waves else ""))
    return sim_dir, build_dir


def simulate(
    name: str, simulator: str, run_dir: Path, test_filter: str | None, waves: bool
) -> dict[str, Any]:
    """Build and run the cocotb tests; the result is the `sim` part of the verb's result."""
    test_dir = WORK / "test"
    out_dir = run_dir / "out"
    if not (test_dir / "tb.v").is_file():
        return _outcome("no_testbench", message="test/tb.v is missing (create_project writes it)")
    modules = discover_modules(test_dir)
    if not modules:
        return _outcome("no_modules", message="there is no test/test*.py file with tests in it")
    sim_dir, build_dir = _prepare_dirs(simulator, waves, run_dir)
    build_log, test_log = sim_dir / "build.log", sim_dir / "test.log"
    results_xml = sim_dir / "results.xml"
    from cocotb_tools.runner import get_runner  # in the image only

    os.environ.setdefault("COCOTB_ANSI_OUTPUT", "0")
    runner = get_runner(simulator)
    build_args = ["--timing", "-Wno-fatal"] if simulator == "verilator" else []
    if simulator == "verilator" and waves:
        build_args.append("--trace-fst")
    sources = [out_dir / "project.v", out_dir / f"{name}.v", test_dir / "tb.v"]
    try:
        runner.build(
            sources=sources,
            hdl_toplevel="tb",
            build_dir=build_dir,
            timescale=("1ns", "1ps"),
            build_args=build_args,
            waves=waves,
            always=simulator == "icarus",
            log_file=build_log,
        )
    except (RuntimeError, SystemExit) as exc:
        return _outcome(
            "build_failed",
            log=log_lines(_read_text(build_log), out_dir, name),
            message=clip(str(exc), 200),
        )
    exit_code: int | None = 0
    # The test modules live in /work/test; it goes first so that `test.py` is not shadowed by
    # the standard library's `test` package. cocotb copies sys.path into the simulator's
    # PYTHONPATH when `test()` runs, so it is added only around that call.
    sys.path.insert(0, str(test_dir))
    try:
        runner.test(
            test_module=modules,
            hdl_toplevel="tb",
            build_dir=build_dir,
            test_dir=sim_dir,
            results_xml=str(results_xml),
            waves=waves,
            timescale=("1ns", "1ps"),
            test_filter=test_filter,
            log_file=test_log,
        )
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
    except RuntimeError:
        exit_code = 1
    finally:
        with contextlib.suppress(ValueError):
            sys.path.remove(str(test_dir))
        emit._reap_processes()
    try:
        records = results.parse_junit(results_xml.read_bytes())
    except (OSError, results.ResultsError) as exc:
        return _outcome(
            "no_results",
            exit_code=exit_code,
            log=log_lines(_read_text(test_log), out_dir, name),
            message=clip(f"no usable results.xml: {exc}", 200),
        )
    kept: str | None = None
    note: str | None = None
    if waves:
        source = find_wave_file(simulator, sim_dir, build_dir)
        if source is None:
            note = "the simulation wrote no wave file"
        else:
            kept, note = keep_waves(simulator, source)
    return _outcome(
        "ran",
        tests=records,
        tests_total=len(records),
        waves=kept if kept and len(kept) <= WAVES_PATH_MAX else None,
        exit_code=exit_code,
        message=note,
    )


def run(request: dict[str, Any]) -> dict[str, Any]:
    """The `test` verb. Returns the emit result plus a `sim` entry (None unless emit was ok)."""
    simulator, test_filter, waves = _check(request)
    name, _frontend, _author, run_dir = emit._check_request(request)
    result = emit.emit(request)
    result["sim"] = None
    if not result.get("ok"):
        return result
    sim = simulate(name, simulator, run_dir, test_filter, waves)
    sim["simulator"] = simulator
    result["sim"] = sim
    return result
