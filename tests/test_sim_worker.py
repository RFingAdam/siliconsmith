# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The pure parts of the worker's `test` verb (no simulator needed)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from siliconsmith.worker import emit, sim
from siliconsmith.worker.limits import LOG_LINE_MAX, MAX_LOG_LINES


def request(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "chip",
        "frontend": "verilog",
        "author": "Ada",
        "run_dir": "/work/build/.ss/abc",
        "simulator": "icarus",
    }
    base.update(fields)
    return base


@pytest.mark.parametrize(
    "bad",
    [
        {"simulator": "modelsim"},
        {"simulator": None},
        {"test_filter": 5},
        {"test_filter": "("},
        {"test_filter": "a" * 300},
        {"waves": "yes"},
    ],
)
def test_bad_requests(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        sim.run(request(**bad))


def test_emit_failure_skips_the_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": False, "stage": "lint"})
    monkeypatch.setattr(sim, "simulate", lambda *a: pytest.fail("must not simulate"))
    assert sim.run(request()) == {"ok": False, "stage": "lint", "sim": None, "gl": None}


def test_emit_success_runs_the_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": True, "stage": "done"})
    seen: list[tuple[Any, ...]] = []

    def fake(*args: Any) -> dict[str, Any]:
        seen.append(args)
        return {"state": "ran"}

    monkeypatch.setattr(sim, "simulate", fake)
    result = sim.run(request(test_filter="wrap", waves=True, simulator="verilator"))
    assert result["sim"] == {"state": "ran", "simulator": "verilator"}
    assert seen == [("chip", "verilator", Path("/work/build/.ss/abc"), "wrap", True)]


def test_discover_modules(tmp_path: Path) -> None:
    for name in (
        "test.py",
        "test_a.py",
        "test_b2.py",
        "tb.py",
        "helpers.py",
        "test-x.py",
        "Test.py",
    ):
        (tmp_path / name).write_text("")
    (tmp_path / "test_dir.py").mkdir()
    (tmp_path / "test_link.py").symlink_to(tmp_path / "test.py")
    assert sim.discover_modules(tmp_path) == ["test", "test_a", "test_b2"]


def test_log_lines_prefer_errors_and_map_paths() -> None:
    out = Path("/work/build/.ss/abc/out")
    text = "\n".join(
        [
            "- V e r i l a t i o n   R e p o r t",
            "%Error: /work/build/.ss/abc/out/project.v:12:3: syntax error, unexpected IDENTIFIER",
            "%Error: /work/test/tb.v:3:1: Cannot find file containing module: 'x'",
            "make: *** [Vtop.mk] Error 1",
            "",
        ]
    )
    lines = sim.log_lines(text, out, "chip")
    assert lines[0] == "%Error: src/project.v:12:3: syntax error, unexpected IDENTIFIER"
    assert lines[1] == "%Error: test/tb.v:3:1: Cannot find file containing module: 'x'"
    assert all("/work" not in line for line in lines)


def test_log_lines_fall_back_to_the_tail_and_are_clipped() -> None:
    text = "\n".join(f"line {n}" for n in range(100)) + "\n" + "q" * 2000
    lines = sim.log_lines(text, Path("/work/build/.ss/abc/out"), "chip")
    assert len(lines) == MAX_LOG_LINES
    assert all(len(line) <= LOG_LINE_MAX for line in lines)
    assert lines[-1].endswith("…")
    many = "\n".join(f"error {n}" for n in range(100))
    assert len(sim.log_lines(many, Path("/work/build/.ss/abc/out"), "chip")) == MAX_LOG_LINES


FST_HEAD = b"\x00" + (329).to_bytes(8, "big") + b"\x00" * 400


def test_find_wave_file_prefers_the_testbench_dump(tmp_path: Path) -> None:
    sim_dir, build = tmp_path / "sim", tmp_path / "build"
    sim_dir.mkdir()
    build.mkdir()
    assert sim.find_wave_file("verilator", sim_dir, build) is None
    (sim_dir / "dump.fst").write_bytes(FST_HEAD)
    assert sim.find_wave_file("verilator", sim_dir, build) == sim_dir / "dump.fst"
    (sim_dir / "tb.fst").write_bytes(FST_HEAD)
    assert sim.find_wave_file("verilator", sim_dir, build) == sim_dir / "tb.fst"
    assert sim.find_wave_file("icarus", sim_dir, build) == sim_dir / "tb.fst"
    (sim_dir / "tb.fst").unlink()
    assert sim.find_wave_file("icarus", sim_dir, build) is None
    (build / "tb.vcd").write_text("$date x $end")
    assert sim.find_wave_file("icarus", sim_dir, build) == build / "tb.vcd"


def test_keep_waves_copies_by_content_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    (tmp_path / "build").mkdir()
    source = tmp_path / "tb.fst"
    source.write_text("$date x $end\n$timescale 1ns $end\n")  # a VCD with an FST name
    assert sim.keep_waves("icarus", source) == ("build/waves/icarus/tb.vcd", None)
    assert (tmp_path / "build/waves/icarus/tb.vcd").read_text() == source.read_text()
    source.write_bytes(FST_HEAD)
    assert sim.keep_waves("icarus", source) == ("build/waves/icarus/tb.fst", None)
    assert [p.name for p in (tmp_path / "build/waves/icarus").iterdir()] == ["tb.fst"]
    source.write_bytes(b"nothing useful")
    path, note = sim.keep_waves("icarus", source)
    assert path is None and note is not None and "neither VCD nor FST" in note


def test_keep_waves_refuses_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (build / "waves").symlink_to(elsewhere)
    source = tmp_path / "tb.fst"
    source.write_bytes(FST_HEAD)
    path, note = sim.keep_waves("icarus", source)
    assert path is None and note is not None and "symbolic link" in note
    assert list(elsewhere.iterdir()) == []


def test_keep_waves_refuses_a_symlinked_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    folder = tmp_path / "build" / "waves" / "icarus"
    folder.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (folder / "tb.fst").symlink_to(victim)
    source = tmp_path / "tb.fst"
    source.write_bytes(FST_HEAD)
    assert sim.keep_waves("icarus", source)[0] == "build/waves/icarus/tb.fst"
    assert victim.read_text() == "keep"  # the old link was removed, not written through


def test_log_lines_keep_test_traceback_frames() -> None:
    text = "\n".join(
        [
            "Traceback (most recent call last):",
            '  File "/work/test/test.py", line 7, in <module>',
            "    import nothing",
            "ModuleNotFoundError: No module named 'nothing'",
        ]
    )
    lines = sim.log_lines(text, Path("/work/build/.ss/abc/out"), "chip")
    assert lines == [
        '  File "test/test.py", line 7, in <module>',
        "ModuleNotFoundError: No module named 'nothing'",
    ]


def test_clear_waves_removes_old_traces_without_following_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    folder = tmp_path / "build" / "waves" / "icarus"
    folder.mkdir(parents=True)
    other = tmp_path / "build" / "waves" / "verilator"
    other.mkdir()
    (other / "tb.fst").write_bytes(FST_HEAD)
    (folder / "tb.fst").write_bytes(FST_HEAD)
    (folder / "sub").mkdir()
    (folder / "sub" / "x").write_text("x")
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (folder / "tb.vcd").symlink_to(victim)
    sim.clear_waves("icarus")
    assert list(folder.iterdir()) == []
    assert victim.read_text() == "keep"
    assert (other / "tb.fst").exists()  # another simulator's trace is not this run's business
    sim.clear_waves("icarus")  # and an empty or missing folder is fine
    sim.clear_waves("nothing")


def test_clear_waves_unlinks_a_symlinked_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "tb.fst").write_bytes(FST_HEAD)
    waves = tmp_path / "build" / "waves"
    waves.mkdir(parents=True)
    (waves / "icarus").symlink_to(elsewhere)
    sim.clear_waves("icarus")
    assert not (waves / "icarus").exists() and (elsewhere / "tb.fst").exists()


def test_run_clears_old_waves_even_when_emit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    folder = tmp_path / "build" / "waves" / "icarus"
    folder.mkdir(parents=True)
    (folder / "tb.fst").write_bytes(FST_HEAD)
    monkeypatch.setattr(emit, "_check_request", lambda r: ("chip", "verilog", "a", tmp_path))
    monkeypatch.setattr(emit, "emit", lambda r: {"ok": False})
    assert sim.run(request())["sim"] is None
    assert list(folder.iterdir()) == []


def test_a_symlinked_build_folder_is_a_design_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sim, "WORK", tmp_path)
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "tb.v").write_text("module tb; endmodule\n")
    (tmp_path / "test" / "test.py").write_text("")
    (tmp_path / "build" / "sim").mkdir(parents=True)
    (tmp_path / "build" / "sim" / "icarus").symlink_to(tmp_path)
    outcome = sim.simulate("chip", "icarus", tmp_path / "run", None, False)
    assert outcome["state"] == "build_failed"
    assert "build/sim/icarus" in outcome["log"][0]
