# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The `test` verb: emit (re-run in full), then simulate with cocotb on Icarus or Verilator.
With `gl` it synthesizes the design to sky130 gates first and simulates the netlist on Icarus.

One container call. The emit pipeline in `worker/emit.py` runs first and its result is returned
as it is; only when it is ok does the simulation start. The simulation uses cocotb's Python
runner (`cocotb_tools.runner`) with the Tiny Tapeout testbench `test/tb.v` as top level (`tb`),
the freshly emitted `out/project.v` and `out/<name>.v` as sources, 1 ns / 1 ps timescale and,
for Verilator, `--timing`.

The agent's test modules run inside the simulator process, a child of this one, with the project
`test/` folder first on its Python path. Simulator output goes to log files under the run
folder. Build folders live in `build/sim/` (and ccache in `build/.cache`), so a second run only
rebuilds what changed. With `waves`, the wave file is copied to `build/waves/<simulator>/`.

## Gate-level simulation (`gl`)

Same container call: emit, then the shared synthesis of `worker/synth.py` (same script, same
do-not-use cell lists, same early stop and netlist checks as `synthesize`), then Icarus on
`test/tb.v` + the netlist + the sky130 cell models. The netlist stays in the run folder
(`out/synth/tt_um_<name>.v`); `build/synth` belongs to `synthesize` and is never touched. The
gate-level run has its own folders, so it never disturbs an RTL result: `build/sim/icarus-gl-waves`
(a gate-level run always dumps its waves) and `build/waves/icarus-gl/tb.fst`.

Tiny Tapeout's own gate-level test (the `GATES=yes` branch of the template `test/Makefile`, run
by tt-gds-action) compiles `tb.v` + the post-layout netlist + `primitives.v` +
`sky130_fd_sc_hd.v` with `-DGL_TEST -DFUNCTIONAL -DUSE_POWER_PINS -DSIM -DUNIT_DELAY=#1`. Ours
differs on purpose, and only here:

- The netlist is the Yosys netlist straight after synthesis, not the placed and routed one.
  It has no power pins (`VPWR`/`VGND`), so `USE_POWER_PINS` and `GL_TEST` (which makes `tb.v`
  connect the power pins) are NOT defined. With them, the module would not elaborate.
- The sources are given to the runner as one list (cocotb's Python runner instead of `make`).

The test modules, the `tb` top level, the seed handling and the result parsing are the RTL
run's own.

`cocotb_tools` is imported inside the function that needs it; everything else is stdlib.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import signal
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from siliconsmith.worker import emit, gldiff, pyerror, results, synth
from siliconsmith.worker import waves as wavefile
from siliconsmith.worker.limits import (
    LOG_LINE_MAX,
    MAX_LOG_LINES,
    MESSAGE_MAX,
    WAVES_PATH_MAX,
    clip,
)

WORK = emit.WORK
SIMULATORS = ("icarus", "verilator")
# The folder name of a gate-level run in build/sim and build/waves (next to `icarus`).
GL_KEY = "icarus-gl"
CELL_MODEL_DIR = "/opt/pdk/sky130A/libs.ref/sky130_fd_sc_hd/verilog"
CELL_MODELS = ("primitives.v", "sky130_fd_sc_hd.v")
# What `GATES=yes` defines in Tiny Tapeout's Makefile, minus GL_TEST and USE_POWER_PINS (see the
# module docstring). Values are what cocotb's runner turns into `-DNAME=value`.
GL_DEFINES = {"FUNCTIONAL": "1", "SIM": "1", "UNIT_DELAY": "#1"}
MODULE_RE = re.compile(r"^test[A-Za-z0-9_]*$")
MAX_FILTER_CHARS = 200
MAX_WAVE_BYTES = 256 * 1024 * 1024
WAVE_EXTENSIONS = {"fst": ".fst", "vcd": ".vcd"}
_ERROR_LINE = re.compile(r"error|fatal|cannot|not found|undefined|no such", re.IGNORECASE)
_TEST_FRAME = re.compile(r'^\s*File "test/[^"]*", line \d+')


def _check(request: dict[str, Any]) -> tuple[str, str | None, bool, bool]:
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
    gl = request.get("gl", False)
    if not isinstance(gl, bool):
        raise ValueError("gl must be true or false")
    if gl and simulator != "icarus":
        raise ValueError("gate-level simulation runs on icarus only")
    return str(simulator), test_filter, waves, gl


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


def log_lines(text: str, out_dir: Path, name: str, netlist: Path | None = None) -> list[str]:
    """The lines worth showing for a failed build or a crashed simulation (clipped, at most 15).

    Lines that look like errors come first; if there are none, the last lines of the log. The
    last two traceback frames in the project's own test files (`File "test/x.py", line N`) are
    kept too, so a Python error in a test comes back with its file and line.
    """
    if netlist is not None:
        text = text.replace(str(netlist), "the gate-level netlist")
    mapped = [ln.rstrip() for ln in map_log_paths(text, out_dir, name).splitlines() if ln.strip()]
    errors = [i for i, ln in enumerate(mapped) if _ERROR_LINE.search(ln)]
    if not errors:
        return [clip(ln, LOG_LINE_MAX) for ln in mapped[-MAX_LOG_LINES:]]
    frames = [i for i, ln in enumerate(mapped) if _TEST_FRAME.match(ln)][-2:]
    room = max(MAX_LOG_LINES - len(frames), 1)
    chosen = sorted(set(errors[:room]) | set(frames))[:MAX_LOG_LINES]
    return [clip(mapped[i], LOG_LINE_MAX) for i in chosen]


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


def clear_waves(simulator: str) -> None:
    """Empty `build/waves/<simulator>/` so a later `inspect_waves` cannot read an older run's
    trace. Symlinks are removed, never followed. Problems are ignored: keeping a new wave file
    reports them."""
    folder = WORK / "build" / "waves" / simulator
    with contextlib.suppress(OSError):
        if folder.parent.is_symlink() or not folder.parent.exists():
            return
        if folder.is_symlink():
            folder.unlink()
            return
        if not folder.is_dir():
            return
        for old in folder.iterdir():
            if old.is_dir() and not old.is_symlink():
                shutil.rmtree(old, ignore_errors=True)
            else:
                old.unlink()


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


def _prepare_dirs(
    key: str, waves: bool, run_dir: Path, scratch: Path | None = None
) -> tuple[Path, Path]:
    if scratch is not None:  # a reference run: everything inside the run folder
        sim_dir = scratch / "sim"
        sim_dir.mkdir(parents=True, exist_ok=True)
        build_dir = scratch / "build"
        build_dir.mkdir(exist_ok=True)
        return sim_dir, build_dir
    sim_dir = run_dir / "sim"
    sim_dir.mkdir(parents=True, exist_ok=True)
    build_dir = _mkdir_nofollow(WORK / "build", "sim", key + ("-waves" if waves else ""))
    return sim_dir, build_dir


def simulate(
    name: str,
    simulator: str,
    run_dir: Path,
    test_filter: str | None,
    waves: bool,
    netlist: Path | None = None,
    *,
    keep: bool | None = None,
    keep_on_failure: bool = False,
    scratch: Path | None = None,
) -> dict[str, Any]:
    """Build and run the cocotb tests; the result is the `sim` part of the verb's result.

    With `netlist` (a gate-level run, Icarus only) the design is that netlist plus the sky130
    cell models instead of the emitted RTL, and the build, sim and wave folders are the
    `icarus-gl` ones. `waves` dumps a wave file; it is copied to `build/waves/` when `keep`
    (default: when `waves`) or when `keep_on_failure` and a test failed. With `scratch` (a
    folder in the run folder) the build and sim folders are there and nothing is kept under
    `build/`: the reference RTL run of a gate-level failure report. For a gate-level or scratch
    run the wave file's path is in the private key `wave_file`, which the caller removes.
    """
    keep = waves if keep is None else keep
    test_dir = WORK / "test"
    out_dir = run_dir / "out"
    key = GL_KEY if netlist is not None else simulator
    if not (test_dir / "tb.v").is_file():
        return _outcome("no_testbench", message="test/tb.v is missing (create_project writes it)")
    modules = discover_modules(test_dir)
    if not modules:
        return _outcome("no_modules", message="there is no test/test*.py file with tests in it")
    try:
        sim_dir, build_dir = _prepare_dirs(key, waves, run_dir, scratch)
    except OSError as exc:
        return _outcome(
            "build_failed",
            log=[clip(f"build/sim/{key} cannot be used: {exc}", LOG_LINE_MAX)],
            message="the build folder is not a plain folder",
        )
    build_log, test_log = sim_dir / "build.log", sim_dir / "test.log"
    results_xml = sim_dir / "results.xml"
    from cocotb_tools.runner import get_runner  # in the image only

    os.environ.setdefault("COCOTB_ANSI_OUTPUT", "0")
    runner = get_runner(simulator)
    build_args = ["--timing", "-Wno-fatal"] if simulator == "verilator" else []
    if simulator == "verilator" and waves:
        build_args.append("--trace-fst")
    defines: dict[str, str] = {}
    if netlist is None:
        sources = [out_dir / "project.v", out_dir / f"{name}.v", test_dir / "tb.v"]
    else:
        models = [Path(CELL_MODEL_DIR) / model for model in CELL_MODELS]
        missing = [m.name for m in models if not m.is_file()]
        if missing:
            return _outcome(
                "build_failed",
                message="the sky130 cell models are missing from the toolchain image",
                log=[f"missing: {', '.join(missing)}"],
            )
        # Tiny Tapeout's template Makefile lists the cell models, the netlist and then tb.v.
        # Ours is tb.v first: it works because the cell models reset `default_nettype
        # themselves, and it keeps the testbench the first (top-level) source.
        sources = [test_dir / "tb.v", *models, netlist]
        defines = dict(GL_DEFINES)
    try:
        runner.build(
            sources=sources,
            hdl_toplevel="tb",
            build_dir=build_dir,
            timescale=("1ns", "1ps"),
            build_args=build_args,
            defines=defines,
            waves=waves,
            always=simulator == "icarus",
            log_file=build_log,
        )
    except (RuntimeError, SystemExit) as exc:
        return _outcome(
            "build_failed",
            log=log_lines(_read_text(build_log), out_dir, name, netlist),
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
        why = str(exc) if isinstance(exc, results.ResultsError) else "the file is missing"
        return _outcome(
            "no_results",
            exit_code=exit_code,
            log=log_lines(_read_text(test_log), out_dir, name, netlist),
            message=clip(f"the simulator wrote no usable results.xml ({why})", 200),
        )
    kept: str | None = None
    note: str | None = None
    source: Path | None = None
    if waves:
        source = find_wave_file(simulator, sim_dir, build_dir)
        failed = any(r["outcome"] in ("fail", "error") for r in records)
        if source is None:
            note = "the simulation wrote no wave file"
        elif scratch is None and (keep or (keep_on_failure and failed)):
            kept, note = keep_waves(key, source)
    outcome = _outcome(
        "ran",
        tests=records,
        tests_total=len(records),
        waves=kept if kept and len(kept) <= WAVES_PATH_MAX else None,
        exit_code=exit_code,
        message=note,
    )
    if scratch is not None or netlist is not None:
        outcome["wave_file"] = source
    return outcome


def _gl_part(synthesis: dict[str, Any]) -> dict[str, Any]:
    """The `gl` entry of the result: what was defined, and the synthesis outcome."""
    return {
        "defines": dict(GL_DEFINES),
        "synth": synthesis,
        "netlist_sha256": synthesis.get("netlist_sha256"),
        "simulated": False,
    }


def _synthesis_blocks_simulation(synthesis: dict[str, Any]) -> bool:
    """Synthesis did not give a netlist worth simulating: it stopped, or `synthesize` would fail
    it for a netlist check or a cell Tiny Tapeout does not allow."""
    return bool(synthesis["state"] != "ran" or synthesis["checks"] or synthesis["forbidden_cells"])


def _run_gl(
    request: dict[str, Any],
    result: dict[str, Any],
    started: float,
    test_filter: str | None,
    waves: bool,
) -> dict[str, Any]:
    name, frontend, _author, run_dir = emit._check_request(request)
    deadline = started + max(synth._number(request, "timeout_s") - synth.RESERVE_S, 1.0)
    synthesis = synth.synthesize(
        name,
        frontend,
        run_dir,
        synth._number(request, "clock_period_ns"),
        synth._number(request, "stop_area_um2"),
        deadline,
    )
    if isinstance(synthesis.get("message"), str):
        synthesis["message"] = clip(synthesis["message"], MESSAGE_MAX)
    gl = _gl_part(synthesis)
    result["gl"] = gl
    if _synthesis_blocks_simulation(synthesis):
        return result
    netlist = run_dir / "out" / synth.SYNTH_DIR / f"tt_um_{name}.v"
    # The RTL reference run (if a test fails) must see the same random numbers as this run.
    seed = os.environ.setdefault("COCOTB_RANDOM_SEED", str(int(time.time())))
    began = time.monotonic()
    # Always dump the gates' waves (a failure report needs them); they are kept under
    # build/waves/icarus-gl when the agent asked for waves or when a test failed.
    sim = simulate(
        name, "icarus", run_dir, test_filter, True, netlist, keep=waves, keep_on_failure=True
    )
    sim["simulator"] = "icarus"
    gl_wave = sim.pop("wave_file", None)
    result["sim"] = sim
    gl["simulated"] = True
    gl["seed"] = int(seed) if seed.isdigit() else None
    failed = sim["state"] == "ran" and any(
        t["outcome"] in ("fail", "error") for t in sim.get("tests", [])
    )
    if failed:
        gl["diff"] = _first_difference(
            name, run_dir, test_filter, sim["tests"], gl_wave, time.monotonic() - began, deadline
        )
    return result


class _OutOfTime(BaseException):
    """The wall-clock limit of `_time_limit` passed."""


@contextlib.contextmanager
def _time_limit(seconds: float | None) -> Iterator[None]:
    """Raise `_OutOfTime` in the main thread after `seconds` of wall clock (no limit when None
    or when signals are not available). The interrupted subprocess wait kills the simulator."""
    if seconds is None or not hasattr(signal, "setitimer"):
        yield
        return

    def fire(_signum: int, _frame: object) -> None:
        raise _OutOfTime

    try:
        previous = signal.signal(signal.SIGALRM, fire)
    except ValueError:  # not the main thread
        yield
        return
    signal.setitimer(signal.ITIMER_REAL, max(seconds, 0.1))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# A gate-level simulation slower than this does not get a reference rerun (the RTL one is
# faster, but the whole call must stay inside the host's timeout).
DIFF_MAX_GL_SIM_S = 150.0


def _first_difference(
    name: str,
    run_dir: Path,
    test_filter: str | None,
    tests: list[dict[str, Any]],
    gl_wave: Path | None,
    gl_seconds: float,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Rerun the RTL with the same seed and filter in a scratch folder of the run folder, then
    compare the pins of the two wave files (see `worker/gldiff.py`)."""
    if gl_wave is None:
        return {"state": "unavailable", "reason": "the gate-level run wrote no wave file"}
    if gl_seconds > DIFF_MAX_GL_SIM_S:
        return {"state": "skipped", "reason": "the gate-level run was too slow to run twice"}
    scratch = run_dir / "rtl-reference"
    try:
        with _time_limit(None if deadline is None else deadline - time.monotonic()):
            rtl = simulate(name, "icarus", run_dir, test_filter, True, None, scratch=scratch)
    except _OutOfTime:
        emit._reap_processes()
        return {"state": "unavailable", "reason": "the RTL rerun ran out of time"}
    except Exception as exc:  # the report is a help; its failure must not fail the run
        return {"state": "unavailable", "reason": f"the RTL rerun crashed ({type(exc).__name__})"}
    rtl_wave = rtl.pop("wave_file", None)
    if rtl["state"] != "ran" or rtl_wave is None:
        return {"state": "unavailable", "reason": "the RTL rerun did not produce a wave file"}
    rtl_failed = _same_test_failed(tests, rtl["tests"])
    try:
        found = gldiff.compare_files(rtl_wave, gl_wave, gldiff.failing_end_ns(tests))
    except Exception as exc:  # pywellen problems: report, do not fail
        return {"state": "unavailable", "reason": f"cannot read the waves ({type(exc).__name__})"}
    found["rtl_failed"] = rtl_failed
    return found


def _same_test_failed(gl_tests: list[dict[str, Any]], rtl_tests: list[dict[str, Any]]) -> bool:
    """Whether the first test that failed on the gates (by module and name) also failed on the
    RTL. Another test failing on the RTL says nothing about this one."""
    bad = ("fail", "error")
    first = next((t for t in gl_tests if t.get("outcome") in bad), None)
    if first is None:
        return False
    return any(
        t.get("outcome") in bad
        and t.get("name") == first.get("name")
        and t.get("module") == first.get("module")
        for t in rtl_tests
    )


def run(request: dict[str, Any]) -> dict[str, Any]:
    """The `test` verb. Returns the emit result plus a `sim` entry (None unless emit was ok).

    With `gl` the result also has a `gl` entry (None unless emit was ok) holding `defines`, the
    `synth` outcome (same shape as the `synth` verb's `synth` entry), `netlist_sha256` and
    `simulated`; `sim` stays None when synthesis stopped the run. A gate-level request carries
    the `synth` verb's extra fields `clock_period_ns`, `stop_area_um2` and `timeout_s` (the time
    for the synthesis part).
    """
    started = time.monotonic()
    simulator, test_filter, waves, gl = _check(request)
    name, _frontend, _author, run_dir = emit._check_request(request)
    if gl:  # fail on a bad request before anything runs
        for key in ("clock_period_ns", "stop_area_um2", "timeout_s"):
            synth._number(request, key)
    # Whatever happens next, build/waves holds this run's trace or nothing.
    clear_waves(GL_KEY if gl else simulator)
    result = emit.emit(request)
    result["sim"] = None
    result["gl"] = None
    if not result.get("ok"):
        return result
    if gl:
        return _run_gl(request, result, started, test_filter, waves)
    sim = simulate(name, simulator, run_dir, test_filter, waves)
    sim["simulator"] = simulator
    result["sim"] = sim
    return result
