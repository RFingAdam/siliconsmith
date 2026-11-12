# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Real container tests: the image, the exact `docker run` flags, and the toolchain_info tool."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import REPO_ROOT
from mcp import Client

from integration.helpers import hardened
from siliconsmith.runners.docker import DockerRunner
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
    # repo_digest is null for a local build.
    optional = {"repo_digest"}
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


PDK_OPEN_PDKS = "8afc8346a57fe1ab7934ba5a6056ea8b43078e71"
# sha256 of each baked-in file, fixed here so the test does not just trust the image's own manifest.
PDK_FILE_SHA256 = {
    "sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib": (
        "8e78e14442062dba34d414fca6490b2f6b96038d4510d1438ca44fee31487135"
    ),
    "sky130A/libs.ref/sky130_fd_sc_hd/verilog/primitives.v": (
        "7d4b1441d06b0f2b9662befa675a0484d77149e04eb42a5f1fa2d4cfded7b2ce"
    ),
    "sky130A/libs.ref/sky130_fd_sc_hd/verilog/sky130_fd_sc_hd.v": (
        "12d5d6448ccf265c544d88402d5c10ab6de9bee8b8cde48eee1f31ea8c3c3e45"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/csa_map.v": (
        "d9c1a8b753da464c21eadd37075774b30841d7f16da42f33847000a732d78628"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/drc_exclude.cells": (
        "8785391a0540d4b96b52b242dc57bf337860e607eed09a25664673f710a9afb7"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/fa_map.v": (
        "0a1be76f4b8308339a02fbb752097ee705eeb1ee08e59eb2477fbd728344deaa"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/latch_map.v": (
        "8d30955f53fa3968f7cce5c89e629addffe91280818df41b709d5fa961ddb681"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/no_synth.cells": (
        "8bd5ee6d949870fd389d177d4b987eeb4d22b55614eea3de8a8f6705fd8982be"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/rca_map.v": (
        "ed6af234c2084f4de5db24cd62ad2c5ee507e8403a61a7e8a39ec8967d4d3d05"
    ),
    "sky130A/libs.tech/openlane/sky130_fd_sc_hd/tribuff_map.v": (
        "18f1fee2ded4cf62ea9e37557c46bea1d34050c431311dca8d04b59416cb16f6"
    ),
}


async def test_toolchain_info_pdk(image: str, runner: DockerRunner, tmp_path: Path) -> None:
    async with Client(build_server(tmp_path, None, runner=runner, image=image)) as client:
        result = await client.call_tool("toolchain_info", {})
    out = result.structured_content
    assert out is not None
    assert out["details"]["pdk"] == {
        "name": "sky130A",
        "open_pdks": PDK_OPEN_PDKS,
        "libraries": ["sky130_fd_sc_hd"],
        "corner": "tt_025C_1v80",
    }
    assert "pdk_note" not in out["details"]


def test_pdk_files_present(image: str) -> None:
    """Every file in /opt/pdk/manifest.json exists and matches its sha256; nothing else is there."""
    script = (
        'cd /opt/pdk && sha256sum -c --quiet <(python3 -c "import json;'
        "[print(h+'  '+f) for f,h in json.load(open('manifest.json'))['files'].items()]\") "
        "&& find . -type f ! -name manifest.json | wc -l && cat manifest.json"
    )
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network=none", "--entrypoint", "bash", image, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    count, _, manifest_text = proc.stdout.partition("\n")
    manifest = json.loads(manifest_text)
    assert manifest["open_pdks"] == PDK_OPEN_PDKS
    assert int(count) == len(manifest["files"]) == 10
    assert all(len(h) == 64 for h in manifest["files"].values())
    assert manifest["files"] == PDK_FILE_SHA256


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
