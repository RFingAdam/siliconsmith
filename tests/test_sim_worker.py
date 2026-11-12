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


# -- gate-level runs -------------------------------------------------------------------------

GL_FIELDS = {"gl": True, "clock_period_ns": 100, "stop_area_um2": 29688.0, "timeout_s": 900}
SYNTH_RAN: dict[str, Any] = {
    "state": "ran",
    "checks": [],
    "forbidden_cells": [],
    "netlist_sha256": "ab" * 32,
}


def test_gl_with_verilator_is_a_bad_request() -> None:
    with pytest.raises(ValueError, match="icarus only"):
        sim.run(request(simulator="verilator", **GL_FIELDS))
    with pytest.raises(ValueError, match="gl must be"):
        sim.run(request(gl="yes"))


@pytest.mark.parametrize("missing", ["clock_period_ns", "stop_area_um2", "timeout_s"])
def test_gl_needs_the_synthesis_numbers_before_anything_runs(
    missing: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: pytest.fail("must not emit"))
    fields = {k: v for k, v in GL_FIELDS.items() if k != missing}
    with pytest.raises(ValueError, match=missing):
        sim.run(request(**fields))


def test_gl_runs_emit_then_synthesis_then_the_netlist_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    def fake_emit(req: dict[str, Any]) -> dict[str, Any]:
        order.append("emit")
        return {"ok": True, "stage": "done"}

    def fake_synth(*args: Any) -> dict[str, Any]:
        order.append("synth")
        assert args[:4] == ("chip", "verilog", Path("/work/build/.ss/abc"), 100.0)
        assert args[4] == 29688.0
        return dict(SYNTH_RAN, message="x" * 5000)

    def fake_simulate(*args: Any) -> dict[str, Any]:
        order.append("simulate")
        return {"state": "ran"}

    monkeypatch.setattr(emit, "emit", fake_emit)
    monkeypatch.setattr(sim.synth, "synthesize", fake_synth)
    monkeypatch.setattr(sim, "simulate", fake_simulate)
    waves: list[str] = []
    monkeypatch.setattr(sim, "clear_waves", waves.append)
    result = sim.run(request(test_filter="wrap", waves=True, **GL_FIELDS))
    assert order == ["emit", "synth", "simulate"]
    assert waves == ["icarus-gl"]  # only the gate-level wave folder is cleared
    assert result["sim"] == {"state": "ran", "simulator": "icarus"}
    gl = result["gl"]
    assert gl["defines"] == {"FUNCTIONAL": "1", "SIM": "1", "UNIT_DELAY": "#1"}
    assert gl["simulated"] is True and gl["netlist_sha256"] == "ab" * 32
    assert len(gl["synth"]["message"]) <= 2000


def test_gl_simulates_the_netlist_in_the_run_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": True})
    monkeypatch.setattr(sim.synth, "synthesize", lambda *a: dict(SYNTH_RAN))
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(sim, "simulate", lambda *a: seen.append(a) or {"state": "ran"})
    monkeypatch.setattr(sim, "clear_waves", lambda key: None)
    sim.run(request(**GL_FIELDS))
    assert seen == [
        (
            "chip",
            "icarus",
            Path("/work/build/.ss/abc"),
            None,
            False,
            Path("/work/build/.ss/abc/out/synth/tt_um_chip.v"),
        )
    ]


@pytest.mark.parametrize(
    "stopped",
    [
        {"state": "too_big", "checks": [], "forbidden_cells": []},
        {"state": "yosys_failed", "checks": [], "forbidden_cells": []},
        {"state": "timeout", "checks": [], "forbidden_cells": []},
        {"state": "ran", "checks": [{"kind": "comb_loop"}], "forbidden_cells": []},
        {"state": "ran", "checks": [], "forbidden_cells": ["sky130_fd_sc_hd__ha_1"]},
    ],
)
def test_gl_does_not_simulate_when_synthesis_stops_or_finds_problems(
    stopped: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": True})
    monkeypatch.setattr(sim.synth, "synthesize", lambda *a: dict(stopped))
    monkeypatch.setattr(sim, "simulate", lambda *a: pytest.fail("must not simulate"))
    monkeypatch.setattr(sim, "clear_waves", lambda key: None)
    result = sim.run(request(**GL_FIELDS))
    assert result["sim"] is None
    assert result["gl"]["simulated"] is False and result["gl"]["synth"] == stopped


def test_gl_emit_failure_stops_before_synthesis(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": False, "stage": "lint"})
    monkeypatch.setattr(sim.synth, "synthesize", lambda *a: pytest.fail("must not synthesize"))
    monkeypatch.setattr(sim, "clear_waves", lambda key: None)
    assert sim.run(request(**GL_FIELDS)) == {"ok": False, "stage": "lint", "sim": None, "gl": None}


def test_an_rtl_run_clears_only_its_own_wave_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emit, "emit", lambda req: {"ok": False})
    waves: list[str] = []
    monkeypatch.setattr(sim, "clear_waves", waves.append)
    sim.run(request())
    sim.run(request(simulator="verilator"))
    assert waves == ["icarus", "verilator"]


class FakeCocotbRunner:
    """Stands in for cocotb's Icarus runner: records the build, writes a results.xml."""

    def __init__(self) -> None:
        self.build_args: dict[str, Any] = {}
        self.test_args: dict[str, Any] = {}

    def build(self, **kwargs: Any) -> None:
        self.build_args = kwargs

    def test(self, **kwargs: Any) -> None:
        self.test_args = kwargs
        xml = Path(__file__).parent / "fixtures" / "results" / "seeded_bug.xml"
        Path(kwargs["results_xml"]).write_bytes(xml.read_bytes())
        (Path(kwargs["test_dir"]) / "tb.fst").write_bytes(FST_HEAD)


@pytest.fixture
def fake_cocotb(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeCocotbRunner:
    import sys
    import types

    fake = FakeCocotbRunner()
    pkg = types.ModuleType("cocotb_tools")
    mod = types.ModuleType("cocotb_tools.runner")
    mod.get_runner = lambda name: fake  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cocotb_tools", pkg)
    monkeypatch.setitem(sys.modules, "cocotb_tools.runner", mod)
    work = tmp_path / "work"
    (work / "test").mkdir(parents=True)
    (work / "test" / "tb.v").write_text("module tb; endmodule\n")
    (work / "test" / "test.py").write_text("")
    (work / "build").mkdir()
    monkeypatch.setattr(sim, "WORK", work)
    models = tmp_path / "models"
    models.mkdir()
    for model in sim.CELL_MODELS:
        (models / model).write_text("// model\n")
    monkeypatch.setattr(sim, "CELL_MODEL_DIR", str(models))
    return fake


def test_gl_build_uses_tiny_tapeouts_defines_and_the_cell_models(
    fake_cocotb: FakeCocotbRunner, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "out" / "synth").mkdir(parents=True)
    netlist = run_dir / "out" / "synth" / "tt_um_chip.v"
    netlist.write_text("module tt_um_chip(); endmodule\n")
    out = sim.simulate("chip", "icarus", run_dir, None, True, netlist)
    assert out["state"] == "ran"
    build = fake_cocotb.build_args
    # What Tiny Tapeout's GATES=yes branch defines, without GL_TEST and USE_POWER_PINS.
    assert build["defines"] == {"FUNCTIONAL": "1", "SIM": "1", "UNIT_DELAY": "#1"}
    assert "USE_POWER_PINS" not in build["defines"] and "GL_TEST" not in build["defines"]
    assert [Path(s).name for s in build["sources"]] == [
        "tb.v",
        "primitives.v",
        "sky130_fd_sc_hd.v",
        "tt_um_chip.v",
    ]
    assert build["hdl_toplevel"] == "tb" and build["waves"] is True
    # Own build folder, and the wave file goes to the gate-level folder.
    assert build["build_dir"].name == "icarus-gl-waves"
    assert fake_cocotb.test_args["test_module"] == ["test"]
    assert out["waves"] == "build/waves/icarus-gl/tb.fst"
    assert (sim.WORK / "build" / "waves" / "icarus-gl" / "tb.fst").is_file()
    assert not (sim.WORK / "build" / "waves" / "icarus").exists()


def test_rtl_build_has_no_defines_and_keeps_its_own_folders(
    fake_cocotb: FakeCocotbRunner, tmp_path: Path
) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "out").mkdir(parents=True)
    out = sim.simulate("chip", "icarus", run_dir, None, True)
    assert fake_cocotb.build_args["defines"] == {}
    assert [Path(s).name for s in fake_cocotb.build_args["sources"]] == [
        "project.v",
        "chip.v",
        "tb.v",
    ]
    assert fake_cocotb.build_args["build_dir"].name == "icarus-waves"
    assert out["waves"] == "build/waves/icarus/tb.fst"


def test_gl_build_fails_cleanly_when_the_cell_models_are_missing(
    fake_cocotb: FakeCocotbRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sim, "CELL_MODEL_DIR", str(tmp_path / "nowhere"))
    run_dir = tmp_path / "run"
    (run_dir / "out").mkdir(parents=True)
    out = sim.simulate("chip", "icarus", run_dir, None, False, run_dir / "out" / "n.v")
    assert out["state"] == "build_failed" and "cell models" in out["message"]
    assert fake_cocotb.build_args == {}


def test_gl_log_names_the_netlist_in_plain_words() -> None:
    netlist = Path("/work/build/.ss/abc/out/synth/tt_um_chip.v")
    text = f"{netlist}:42: error: Unknown module type: sky130_fd_sc_hd__zzz\n"
    lines = sim.log_lines(text, Path("/work/build/.ss/abc/out"), "chip", netlist)
    assert lines == ["the gate-level netlist:42: error: Unknown module type: sky130_fd_sc_hd__zzz"]
