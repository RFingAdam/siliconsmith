# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The pure parts of the worker's emit pipeline, and the wrapper shared with the host."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from siliconsmith.runners.worker_cache import ensure_worker_cache
from siliconsmith.worker import emit as worker_emit
from siliconsmith.worker import wrapper_gen
from siliconsmith.worker.wrapper_gen import TT_PORTS, check_core_ports
from siliconsmith.wrapper import generate_wrapper

ALL = {port: direction for port, (direction, _) in TT_PORTS.items()}


@pytest.mark.parametrize(
    "ports",
    [ALL, {"ui_in": "input", "uo_out": "output"}, {"clk": "input", "uo_out": "output"}],
)
def test_host_and_worker_wrappers_are_byte_identical(ports: dict[str, str]) -> None:
    for author in ("Ada Lovelace", "Zoë <z@example.com>"):
        assert generate_wrapper("chip", ports, author) == wrapper_gen.generate_wrapper(
            "chip", ports, author
        )


def test_check_core_ports() -> None:
    assert check_core_ports({"ui_in": ("input", 8), "uo_out": ("output", 8)}) == []
    problems = check_core_ports(
        {
            "ui_in": ("input", 4),
            "uo_out": ("input", 8),
            "foo": ("input", 1),
            "rst": ("input", 1),
            "clk": ("output", 1),
        }
    )
    assert len(problems) == 5
    assert "must be 8 bit(s) wide, not 4" in problems[0]
    assert "must be an output, not an input" in problems[1]
    assert "'foo' is not a Tiny Tapeout pin" in problems[2]
    assert "must be an input, not an output" in problems[4]


@pytest.fixture
def fake_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(worker_emit, "WORK", tmp_path / "work")
    (tmp_path / "work" / "build" / ".ss" / "r1").mkdir(parents=True)
    (tmp_path / "work" / "src").mkdir()
    return tmp_path / "work"


def request(work: Path, **fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "chip",
        "frontend": "verilog",
        "author": "Ada",
        "run_dir": str(work / "build" / ".ss" / "r1"),
    }
    return base | fields


@pytest.mark.parametrize(
    "fields",
    [
        {"name": "Chip"},
        {"name": "../x"},
        {"frontend": "vhdl"},
        {"author": "a\nb"},
        {"author": ""},
        {"run_dir": "/tmp/x"},
    ],
)
def test_bad_requests_are_refused(fake_work: Path, fields: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        worker_emit.emit(request(fake_work, **fields))


def test_missing_verilog_core_is_a_design_error_naming_the_file(fake_work: Path) -> None:
    result = worker_emit.emit(request(fake_work))
    assert result["ok"] is False and result["stage"] == "elaborate"
    assert result["python_error"]["file"] == "src/chip.v"
    assert "does not exist" in result["python_error"]["msg"]


def test_missing_design_file_for_python_frontends(fake_work: Path) -> None:
    for frontend in ("myhdl",):  # amaranth needs the container's seed script first
        result = worker_emit.emit(request(fake_work, frontend=frontend))
        assert result["python_error"]["file"] == "hdl/chip.py"
        assert result["ok"] is False


def test_project_paths_in_tool_text(tmp_path: Path) -> None:
    out = Path("/work/build/.ss/r1/out")
    text = f"{out}/chip.v:3: bad; chip.v:4:5 worse; project.v:1 /work/hdl/chip.py other.v:2"
    shown = worker_emit._project_files(worker_emit._out_paths(text, "chip", out), "chip")
    assert shown == (
        "src/chip.v:3: bad; src/chip.v:4:5 worse; src/project.v:1 hdl/chip.py other.v:2"
    )
    assert worker_emit._lint_file("chip.v", "chip", out) == "src/chip.v"
    assert worker_emit._lint_file(f"{out}/project.v", "chip", out) == "src/project.v"
    assert worker_emit._lint_file("/work/src/other.v", "chip", out) == "src/other.v"


def test_ports_json_is_in_tt_order() -> None:
    ports = {
        "rst_n": ("input", 1),
        "foo": ("input", 2),
        "uo_out": ("output", 8),
        "ui_in": ("input", 8),
    }
    assert list(worker_emit._ports_json(ports)) == ["ui_in", "uo_out", "rst_n", "foo"]


def test_every_worker_module_imports_without_host_packages(tmp_path: Path) -> None:
    """The emit modules must import in the container, which has only the stdlib and the image."""
    cache = ensure_worker_cache(home=tmp_path / "cache")
    script = (
        "import sys\n"
        "for name in ('mcp', 'pydantic', 'yaml', 'anyio', 'siliconsmith.server', "
        "'siliconsmith.context', 'siliconsmith.result', 'siliconsmith.tools', "
        "'siliconsmith.runners', 'siliconsmith.cli', 'siliconsmith.wrapper', "
        "'siliconsmith.paths', 'siliconsmith.project'):\n"
        "    sys.modules[name] = None\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import siliconsmith.worker.emit, siliconsmith.worker.lint\n"
        "import siliconsmith.worker.pyerror, siliconsmith.worker.wrapper_gen\n"
        "import json\n"
        "loaded = [m for m, v in sys.modules.items() if v is not None and "
        "m.startswith('siliconsmith')]\n"
        "print(json.dumps(sorted(loaded)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-I", "-c", script, str(cache)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout)
    assert "siliconsmith.worker.emit" in loaded
    assert not any(m in loaded for m in ("siliconsmith.paths", "siliconsmith.wrapper"))


# -- elaboration runs in a child process --------------------------------------------------------


def test_elaboration_without_the_toolchain_library_is_internal_not_a_design_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from siliconsmith.worker import elaborate

    monkeypatch.setitem(sys.modules, "amaranth", None)
    monkeypatch.setitem(sys.modules, "myhdl", None)
    for frontend in ("amaranth", "myhdl"):
        out = elaborate.run({"frontend": frontend, "name": "chip", "run_dir": str(tmp_path)})
        assert list(out) == ["internal"]
        assert frontend in out["internal"]


def test_a_child_that_vanishes_is_a_design_error_and_internal_is_a_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "work"
    (work / "hdl").mkdir(parents=True)
    (work / "hdl" / "chip.py").write_text("import os\nos._exit(0)\n")
    monkeypatch.setattr(worker_emit, "WORK", work)
    run_dir = work / "build" / ".ss" / "r1"
    run_dir.mkdir(parents=True)
    fake = tmp_path / "fake.py"
    fake.write_text("import sys\nsys.stdin.read()\n")  # exits without writing elab.json
    real_run = subprocess.run

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return real_run([sys.executable, str(fake)], **kwargs)  # type: ignore[call-overload, no-any-return]

    monkeypatch.setattr(worker_emit.subprocess, "run", fake_run)
    body, error = worker_emit._elaborate("chip", "myhdl", run_dir)
    assert body is None and error is not None
    assert error["file"] == "hdl/chip.py" and "before it produced Verilog" in error["msg"]
    (run_dir / "elab.json").write_text(json.dumps({"internal": "ImportError: x"}))
    fake.write_text(
        f"import sys\nsys.stdin.read()\nopen({str(run_dir / 'elab.json')!r}, 'w')"
        '.write(\'{"internal": "ImportError: x"}\')\n'
    )
    with pytest.raises(RuntimeError, match="ImportError"):
        worker_emit._elaborate("chip", "myhdl", run_dir)


def test_reaping_is_off_outside_the_container(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[object] = []
    monkeypatch.setattr(worker_emit.os, "kill", lambda *a: called.append(a))
    monkeypatch.setattr(worker_emit, "WORK", Path("/nonexistent-work"))
    worker_emit._reap_processes()
    assert not called
