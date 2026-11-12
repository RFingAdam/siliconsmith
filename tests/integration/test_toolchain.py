# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Real container tests: the image, the exact `docker run` flags, and the toolchain_info tool."""

from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from mcp import Client

from siliconsmith.runners.docker import DockerRunner, build_argv, readonly_entries
from siliconsmith.runners.worker_cache import ensure_worker_cache
from siliconsmith.server import build_server

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

TOOLS = (
    "iverilog",
    "verilator",
    "yosys",
    "amaranth",
    "amaranth_yosys",
    "myhdl",
    "cocotb",
    "python",
    "pywellen",
)


async def test_toolchain_info_keys(image: str, runner: DockerRunner, tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, None, runner=runner, image=image)) as client:
        result = await client.call_tool("toolchain_info", {})
    assert not result.is_error
    out = result.structured_content
    assert out is not None
    assert out["ok"] is True, out["summary"]
    details = out["details"]
    # repo_digest is null for a local build; pdk is null until M1.
    optional = {"repo_digest", "pdk"}
    for key, value in details.items():
        if key not in optional:
            assert value not in (None, "", {}), f"{key} is empty"
    assert details["image"] == image
    assert details["image_id"].startswith("sha256:")
    assert set(details["versions"]) == set(TOOLS)
    assert all(details["versions"][tool] for tool in TOOLS)
    assert details["versions"]["verilator"].split()[0] >= "5.036"
    assert details["versions"]["cocotb"] == "2.1.0"
    assert details["versions"]["yosys"].startswith("0.62")
    assert details["versions"]["iverilog"].startswith("12.0")
    # CI says whether it pulled the image from GHCR (then there must be a registry digest) or
    # built it itself (then there must not). Unset means a developer run: no claim either way.
    expect = os.environ.get("SILICONSMITH_EXPECT_REPO_DIGEST", "").lower()
    if expect == "true":
        listed = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", image],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert details["repo_digest"] == listed.split("@", 1)[1]
    elif expect == "false":
        assert details["repo_digest"] is None
    else:
        assert "repo_digest" in details


def selftest(runner: DockerRunner, project: Path, **request: object) -> dict[str, object]:
    outcome = runner.run("selftest", request, project_dir=project, timeout_s=120)
    assert outcome.exit_code == 0, outcome.stderr_tail
    assert outcome.result is not None, outcome.stderr_tail
    assert outcome.result["ok"] is True, outcome.result
    return outcome.result


def test_sandbox_as_seen_from_inside(runner: DockerRunner, project: Path) -> None:
    result = selftest(runner, project)
    if not runner.is_rootless() and os.getuid() != 0:
        assert result["uid"] == os.getuid()
        assert result["gid"] == os.getgid()
        assert result["uid"] != 0
    assert result["root_writable"] is False
    # The two checks that really depend on --read-only (a non-root user cannot write `/` anyway).
    assert result["root_mounted_readonly"] is True
    assert result["vartmp_writable"] is False
    assert result["src_writable"] is False
    assert result["hdl_writable"] is False
    assert result["test_writable"] is False
    assert result["build_writable"] is True
    assert result["mounts"] == {
        "hdl": True,
        "src": True,
        "test": True,
        "spec.yaml": True,
        "info.yaml": True,
        "build": True,
    }
    assert result["interfaces"] == ["lo"]
    assert result["home"] == "/tmp/home"
    assert result["env"] == {
        "XDG_CACHE_HOME": "/work/build/.cache",
        "CCACHE_DIR": "/work/build/.cache/ccache",
        "PYTHONDONTWRITEBYTECODE": "1",
        "AMARANTH_USE_YOSYS": "builtin",
        "PYTHONPATH": "/opt/ss",
    }


def test_project_files_are_untouched_and_build_is_ours(runner: DockerRunner, project: Path) -> None:
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    selftest(runner, project)
    after = {
        p: p.read_bytes() for p in project.rglob("*") if p.is_file() and "build" not in p.parts
    }
    assert after == {p: v for p, v in before.items() if "build" not in p.parts}
    assert (project / "build" / ".ss").is_dir()
    if not runner.is_rootless() and os.getuid() != 0:
        assert (project / "build").stat().st_uid == os.getuid()


def test_timeout_kills_the_container(runner: DockerRunner, project: Path) -> None:
    started = time.monotonic()
    outcome = runner.run("selftest", {"sleep_s": 120}, project_dir=project, timeout_s=3)
    assert outcome.timed_out
    assert time.monotonic() - started < 60
    assert outcome.result is None
    name = runner.last_container
    assert name is not None
    deadline = time.monotonic() + 15
    while True:
        listing = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={name}", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        if not listing or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    assert listing == "", "the container is still there after the timeout"


def hardened(image: str, project: Path, *command: str, mount: Path | None = None) -> list[str]:
    """The runner's own `docker run` line (build_argv), with `command` in place of the worker."""
    (project / "build").mkdir(exist_ok=True)
    argv = build_argv(
        container_name=f"ss-test-{uuid.uuid4().hex}",
        image=image,
        verb="unused",
        project_dir=project,
        readonly_entries=readonly_entries(project),
        worker_cache=ensure_worker_cache(),
        user=None if os.getuid() == 0 else (os.getuid(), os.getgid()),
    )
    assert argv[-5:-1] == [image, "python", "-m", "siliconsmith.worker"]
    head = argv[:-5]
    if mount is not None:
        head += ["--mount", f"type=bind,src={mount},dst=/smoke,readonly"]
    return [*head, image, *command]


@pytest.mark.parametrize("simulator", ["icarus", "verilator"])
def test_cocotb_smoke(image: str, project: Path, simulator: str) -> None:
    (project / "build").mkdir(exist_ok=True)
    fixture = REPO_ROOT / "tests" / "fixtures" / "image_smoke"
    proc = subprocess.run(
        hardened(image, project, "python", "/smoke/run_smoke.py", simulator, mount=fixture),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=600,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert f"smoke on {simulator}: 2 tests, 0 failed" in proc.stdout


def test_yosys_and_amaranth_work_under_the_sandbox_flags(image: str, project: Path) -> None:
    (project / "build").mkdir(exist_ok=True)
    script = (
        "from amaranth.hdl import Module, Signal\n"
        "from amaranth.back import verilog\n"
        "m = Module(); a = Signal(8); b = Signal(8); m.d.comb += b.eq(a + 1)\n"
        "print('VERILOG', len(verilog.convert(m, ports=[a, b], name='t', emit_src=False)))\n"
    )
    for command in (["yosys", "-V"], ["python", "-c", script]):
        started = time.monotonic()
        proc = subprocess.run(
            hardened(image, project, *command),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=300,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        print(command[0], f"{time.monotonic() - started:.1f}s")
    cache = project / "build" / ".cache"
    assert (cache / "YoWASP").is_dir()
    assert (cache / "wasmtime").is_dir()
