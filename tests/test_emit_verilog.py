# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""emit_verilog with a fake runner that returns canned worker results (no Docker)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.context import ServerContext
from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.docker import DockerUnavailableError, ImageMissingError
from siliconsmith.scaffold import create_project
from siliconsmith.server import build_server
from siliconsmith.tools.emit_verilog import emit
from siliconsmith.worker.wrapper_gen import TT_PORTS
from siliconsmith.wrapper import generate_wrapper

RUN_ID = "0123456789abcdef0123456789abcdef"
CORE = "// SPDX-FileCopyrightText: Ada\nmodule chip(input clk, output [7:0] uo_out);\nendmodule\n"
ALL_PORTS = {p: {"direction": d, "width": w} for p, (d, w) in TT_PORTS.items()}
CORE_PORTS = {
    "ui_in": {"direction": "input", "width": 8},
    "uo_out": {"direction": "output", "width": 8},
}


def worker_result(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ok": True,
        "verb": "emit",
        "stage": "done",
        "run_id": RUN_ID,
        "ports": CORE_PORTS,
        "wrapper_ports": ALL_PORTS,
        "lint": [],
        "lint_total": 0,
        "python_error": None,
        "message": None,
        "core_generated": True,
    }
    base.update(fields)
    return base


def outcome(result: dict[str, Any] | None, **fields: Any) -> WorkerOutcome:
    values: dict[str, Any] = {
        "exit_code": 0,
        "result": result,
        "stdout_tail": "",
        "stderr_tail": "",
        "timed_out": False,
        "run_id": RUN_ID,
    }
    values.update(fields)
    return WorkerOutcome(**values)


class EmitRunner(FakeRunner):
    """Like the real runner, leaves the worker's output files under build/.ss/<id>/out."""

    def __init__(
        self,
        *outcomes: WorkerOutcome,
        core: str | None = CORE,
        wrapper: str = "// the container's own wrapper: must never be copied\n",
        error: Exception | None = None,
    ) -> None:
        super().__init__(*outcomes)
        self.core = core
        self.wrapper = wrapper
        self.error = error

    def run(self, verb: str, request: Any, *, project_dir: Path, timeout_s: float) -> WorkerOutcome:
        if self.error is not None:
            raise self.error
        out = project_dir / "build" / ".ss" / RUN_ID / "out"
        out.mkdir(parents=True)
        if self.core is not None:
            (out / "chip.v").write_text(self.core)
        (out / "project.v").write_text(self.wrapper)
        return super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)


def make(tmp_path: Path, frontend: str, runner: FakeRunner) -> tuple[ServerContext, Path]:
    project, _ = create_project(tmp_path, "chip", frontend, "Ada Lovelace", "A chip.")
    return ServerContext(root=tmp_path, default_frontend=None, runner=runner), project.dir


def test_success_writes_core_and_regenerated_wrapper(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(worker_result()))
    ctx, project = make(tmp_path, "amaranth", runner)
    result = emit(ctx, "chip")
    assert result.ok, result.summary
    assert (project / "src" / "chip.v").read_text() == CORE
    wrapper = (project / "src" / "project.v").read_text()
    assert wrapper == generate_wrapper(
        "chip", {"ui_in": "input", "uo_out": "output"}, "Ada Lovelace"
    )
    assert "never be copied" not in wrapper
    assert [a.path for a in result.artifacts] == ["src/chip.v", "src/project.v"]
    assert result.details["stage"] == "done"
    assert result.details["ports"] == CORE_PORTS
    assert result.details["top"] == "tt_um_chip"
    assert "Lint: clean" in result.summary
    # One container call, the right verb, request fields and 300 s timeout.
    ((verb, request, run_project, timeout),) = runner.calls
    assert (verb, timeout, run_project) == ("emit", 300.0, project)
    assert request == {"name": "chip", "frontend": "amaranth", "author": "Ada Lovelace"}
    # The worker's run folder is gone afterwards.
    assert not list((project / "build" / ".ss").glob("*"))


def test_verilog_core_is_never_overwritten(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(worker_result(core_generated=False)))
    ctx, project = make(tmp_path, "verilog", runner)
    before = (project / "src" / "chip.v").read_bytes()
    result = emit(ctx, "chip")
    assert result.ok, result.summary
    assert (project / "src" / "chip.v").read_bytes() == before
    assert (project / "src" / "project.v").read_text().startswith("// SPDX-FileCopyrightText: Ada")
    assert [a.kind for a in result.artifacts] == ["rtl", "wrapper"]


def test_python_error_from_the_container(tmp_path: Path) -> None:
    error = {
        "kind": "NameError",
        "file": "hdl/chip.py",
        "line": 12,
        "col": None,
        "msg": "NameError: name 'x' is not defined",
        "source": "m.d.comb += x",
    }
    runner = EmitRunner(outcome(worker_result(ok=False, stage="elaborate", python_error=error)))
    ctx, project = make(tmp_path, "amaranth", runner)
    before = (project / "src" / "project.v").read_bytes()
    result = emit(ctx, "chip")
    assert not result.ok
    assert "hdl/chip.py:12: NameError: name 'x' is not defined" in result.summary
    assert "    m.d.comb += x" in result.summary
    assert result.details["python_error"]["line"] == 12
    assert "hdl/chip.py:12" in result.next_hints[0]
    assert len(result.summary.splitlines()) <= 20
    assert (project / "src" / "project.v").read_bytes() == before  # nothing written


def test_syntax_error_is_found_on_the_host_without_a_container(tmp_path: Path) -> None:
    runner = EmitRunner()
    ctx, project = make(tmp_path, "myhdl", runner)
    (project / "hdl" / "chip.py").write_text(
        "from myhdl import block\n\n@block\ndef chip(:\n  pass\n"
    )
    result = emit(ctx, "chip")
    assert not result.ok
    assert runner.calls == []
    error = result.details["python_error"]
    assert (error["kind"], error["file"], error["line"]) == ("SyntaxError", "hdl/chip.py", 4)
    assert "hdl/chip.py:4:" in result.summary
    assert result.details["stage"] == "elaborate"


def test_non_utf8_source_is_a_design_error(tmp_path: Path) -> None:
    runner = EmitRunner()
    ctx, project = make(tmp_path, "amaranth", runner)
    (project / "hdl" / "chip.py").write_bytes(b"x = '\xff'\n")
    result = emit(ctx, "chip")
    assert not result.ok and runner.calls == []
    assert "not valid UTF-8" in result.summary


def test_ports_stage_failure(tmp_path: Path) -> None:
    message = "the ports of chip do not fit Tiny Tapeout:\nport 'rst' is not a Tiny Tapeout pin"
    runner = EmitRunner(
        outcome(worker_result(ok=False, stage="ports", wrapper_ports=None, message=message))
    )
    ctx, _ = make(tmp_path, "amaranth", runner)
    result = emit(ctx, "chip")
    assert not result.ok
    assert result.details["stage"] == "ports"
    assert "port 'rst' is not a Tiny Tapeout pin" in result.summary


def finding(n: int) -> dict[str, Any]:
    return {
        "severity": "warning",
        "code": "WIDTHTRUNC",
        "file": "src/chip.v",
        "line": n,
        "col": 5,
        "msg": f"Operator ASSIGNW expects 4 bits ({n})",
        "detail": "",
    }


def test_lint_failure_is_structured_and_still_writes_files(tmp_path: Path) -> None:
    lint = [finding(n) for n in range(1, 13)]
    runner = EmitRunner(
        outcome(worker_result(ok=False, stage="lint", lint=lint, lint_total=12)),
    )
    ctx, project = make(tmp_path, "myhdl", runner)
    result = emit(ctx, "chip")
    assert not result.ok
    assert result.details["lint"][0] == finding(1)
    assert result.details["lint_total"] == 12
    assert (
        "src/chip.v:1:5: warning WIDTHTRUNC: Operator ASSIGNW expects 4 bits (1)" in result.summary
    )
    assert "… 4 more in details.lint" in result.summary
    assert len(result.summary.splitlines()) <= 20
    # A generated core: the hint sends the agent to the Python source.
    assert "hdl/chip.py" in result.next_hints[0] and "WIDTHTRUNC" in result.next_hints[0]
    assert (project / "src" / "chip.v").read_text() == CORE


def test_core_syntax_error_reported_as_lint(tmp_path: Path) -> None:
    error = {**finding(2), "severity": "error", "code": "SYNTAX", "msg": "syntax error"}
    runner = EmitRunner(
        outcome(
            worker_result(
                ok=False, stage="lint", ports=None, wrapper_ports=None, lint=[error], lint_total=1
            )
        )
    )
    ctx, _ = make(tmp_path, "verilog", runner)
    result = emit(ctx, "chip")
    assert not result.ok
    assert "src/chip.v:2:5: error SYNTAX: syntax error" in result.summary
    assert result.details["stage"] == "lint"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {"ok": "yes"},
        {"ok": True, "stage": "done"},  # no ports
        worker_result(lint=[{"severity": "info"}]),
        worker_result(ok=True, stage="lint", lint=[finding(1)], lint_total=1),  # ok contradicts
        worker_result(ok=False, stage="done"),
        worker_result(lint_total=0, lint=[finding(1)], stage="lint", ok=False) | {"lint_total": 0},
    ],
)
def test_malformed_result_is_not_ok(tmp_path: Path, bad: dict[str, Any] | None) -> None:
    runner = EmitRunner(outcome(bad, exit_code=0 if bad else 137, stderr_tail="Killed\n"))
    ctx, project = make(tmp_path, "amaranth", runner)
    result = emit(ctx, "chip")
    assert not result.ok
    assert "unreadable result" in result.summary
    assert not (project / "src" / "chip.v").exists()
    if bad is None:
        assert "code 137" in result.summary and "Killed" in result.summary


def test_timeout_is_a_design_failure(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(None, exit_code=-9, timed_out=True))
    ctx, _ = make(tmp_path, "amaranth", runner)
    result = emit(ctx, "chip")
    assert not result.ok
    assert "elaboration did not finish in 300 s" in result.summary
    assert result.details["timed_out"] is True


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (DockerUnavailableError("the docker command was not found; install Docker"), "docker"),
        (
            ImageMissingError("the toolchain image x is not on this machine; run `docker pull x`"),
            "docker pull x",
        ),
    ],
)
def test_infrastructure_errors_raise_tool_errors(
    tmp_path: Path, error: Exception, needle: str
) -> None:
    ctx, _ = make(tmp_path, "amaranth", EmitRunner(error=error))
    with pytest.raises(ToolError, match=needle):
        emit(ctx, "chip")


def test_worker_crash_raises_a_tool_error_naming_the_fix(tmp_path: Path) -> None:
    crash = {"ok": False, "verb": "emit", "error": {"kind": "OSError", "message": "disk full"}}
    ctx, _ = make(tmp_path, "amaranth", EmitRunner(outcome(crash)))
    with pytest.raises(ToolError, match="toolchain_info"):
        emit(ctx, "chip")


def test_host_rechecks_the_ports(tmp_path: Path) -> None:
    lying = worker_result(ports={"ui_in": {"direction": "input", "width": 4}})
    ctx, project = make(tmp_path, "amaranth", EmitRunner(outcome(lying)))
    result = emit(ctx, "chip")
    assert not result.ok
    assert "ui_in" in result.summary and "8 bit" in result.summary
    assert not (project / "src" / "chip.v").exists()


def test_wrapper_ports_must_be_the_tt_pins(tmp_path: Path) -> None:
    short = {k: v for k, v in ALL_PORTS.items() if k != "ena"}
    ctx, _ = make(tmp_path, "amaranth", EmitRunner(outcome(worker_result(wrapper_ports=short))))
    result = emit(ctx, "chip")
    assert not result.ok and "wrapper ports" in result.summary


@pytest.mark.parametrize("poison", ["/home/eve/x", "/work/hdl/x.py"])
def test_generated_core_with_absolute_paths_is_rejected(tmp_path: Path, poison: str) -> None:
    runner = EmitRunner(outcome(worker_result()), core=f"module chip();\n// {poison}\nendmodule\n")
    ctx, project = make(tmp_path, "amaranth", runner)
    result = emit(ctx, "chip")
    assert not result.ok and "absolute path" in result.summary
    assert not (project / "src" / "chip.v").exists()


def test_generated_core_must_define_the_module(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(worker_result()), core="module other();\nendmodule\n")
    ctx, _ = make(tmp_path, "amaranth", runner)
    assert "no 'module chip'" in emit(ctx, "chip").summary


def test_symlinked_output_is_refused(tmp_path: Path) -> None:
    class Evil(EmitRunner):
        def run(
            self, verb: str, request: Any, *, project_dir: Path, timeout_s: float
        ) -> WorkerOutcome:
            result = super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)
            core = project_dir / "build" / ".ss" / RUN_ID / "out" / "chip.v"
            core.unlink()
            core.symlink_to("/etc/passwd")
            return result

    ctx, project = make(tmp_path, "amaranth", Evil(outcome(worker_result())))
    with pytest.raises(ToolError, match="symlink"):
        emit(ctx, "chip")
    assert not (project / "src" / "chip.v").exists()
    assert not list((project / "build" / ".ss").glob("*"))


def test_missing_entry_file_is_a_usage_error(tmp_path: Path) -> None:
    ctx, project = make(tmp_path, "amaranth", EmitRunner())
    os.unlink(project / "hdl" / "chip.py")
    with pytest.raises(ToolError, match="write_rtl"):
        emit(ctx, "chip")


def test_unknown_project(tmp_path: Path) -> None:
    ctx = ServerContext(root=tmp_path, default_frontend=None, runner=EmitRunner())
    with pytest.raises(ToolError, match="unknown project"):
        emit(ctx, "nope")


@pytest.mark.anyio
async def test_tool_through_mcp(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(worker_result()))
    create_project(tmp_path, "chip", "amaranth", "Ada", "A chip.")
    async with Client(build_server(tmp_path, None, runner=runner)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert next(iter(tools["emit_verilog"].input_schema["properties"])) == "project"
        result = await client.call_tool("emit_verilog", {"project": "chip"})
    assert not result.is_error
    assert result.structured_content is not None
    assert set(result.structured_content) == {"ok", "summary", "details", "artifacts", "next_hints"}
    assert result.structured_content["ok"] is True


# -- review fixes ------------------------------------------------------------------------------


def test_over_long_worker_text_is_clipped_not_unreadable() -> None:
    """The worker clips to the host's limits, so a design failure is never 'unreadable'."""
    from siliconsmith.worker.limits import clamp_result

    raw = {
        "ok": False,
        "stage": "ports",
        "ports": None,
        "message": "x" * 5000,
        "lint": [
            {
                "severity": "error",
                "code": "C" * 99,
                "file": "f" * 999,
                "line": 1,
                "col": 1,
                "msg": "m" * 5000,
                "detail": "d" * 5000,
                "hdl_file": "h" * 999,
                "hdl_function": "f" * 250,
            }
        ],
        "python_error": {
            "kind": "K" * 999,
            "file": "f" * 999,
            "line": 1,
            "col": 0,
            "msg": "m" * 5000,
            "source": "s" * 5000,
        },
    }
    from siliconsmith.tools.emit_verilog import _EmitResult

    _EmitResult.model_validate(clamp_result(raw))


def test_many_bad_ports_give_the_ports_failure(tmp_path: Path) -> None:
    from siliconsmith.worker.limits import MESSAGE_MAX
    from siliconsmith.worker.wrapper_gen import check_core_ports, describe_port_problems

    ports = {f"data_in_{i}": ("input", 1) for i in range(40)}
    text = describe_port_problems(check_core_ports(ports))
    assert len(text) < MESSAGE_MAX and "and 32 more" in text
    assert text.count("allowed ports") == 1
    result = worker_result(ok=False, stage="ports", message=text, ports=None, wrapper_ports=None)
    ctx, _ = make(tmp_path, "verilog", EmitRunner(outcome(result)))
    out = emit(ctx, "chip")
    assert out.details["stage"] == "ports"
    assert "unreadable" not in out.summary


def test_missing_run_id_is_unreadable_not_an_assert(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(worker_result(), run_id=None))
    ctx, _ = make(tmp_path, "amaranth", runner)
    assert "unreadable" in emit(ctx, "chip").summary


def test_oversized_generated_core_is_a_design_failure(tmp_path: Path) -> None:
    runner = EmitRunner(outcome(worker_result()), core=CORE + "// pad\n" * 40000)
    ctx, project = make(tmp_path, "amaranth", runner)
    result = emit(ctx, "chip")
    assert not result.ok and "larger than" in result.summary
    assert not (project / "src" / "chip.v").exists()


def test_symlinked_run_root_does_not_delete_outside_files(tmp_path: Path) -> None:
    victim = tmp_path / "victim"
    (victim / "sub").mkdir(parents=True)
    (victim / "notes.txt").write_text("keep")
    (victim / "sub" / "deep.txt").write_text("keep")

    class Evil(EmitRunner):
        def run(
            self, verb: str, request: Any, *, project_dir: Path, timeout_s: float
        ) -> WorkerOutcome:
            result = super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)
            ss = project_dir / "build" / ".ss"
            for child in ss.iterdir():
                for sub in (child / "out").iterdir():
                    sub.unlink()
                (child / "out").rmdir()
                child.rmdir()
            ss.rmdir()
            ss.symlink_to("../../../victim")
            return result

    ctx, project = make(tmp_path, "amaranth", Evil(outcome(worker_result())))
    with pytest.raises(ToolError, match="symlink"):
        emit(ctx, "chip")
    assert sorted(p.name for p in victim.iterdir()) == ["notes.txt", "sub"]
    assert (victim / "sub" / "deep.txt").read_text() == "keep"
    assert not (project / "build" / ".ss").exists()
