# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""toolchain_info with a fake runner (the real one is tested in tests/integration)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import FakeRunner
from mcp import Client

from siliconsmith.runners.base import WorkerOutcome
from siliconsmith.runners.docker import HostStatus, ImageMissingError
from siliconsmith.server import build_server

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _cache_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


IMAGE = "ghcr.io/example/toolchain:ctx-abc"
VERSIONS = {
    "iverilog": "12.0",
    "verilator": "5.046",
    "yosys": "0.62",
    "amaranth": "0.5.10",
    "amaranth_yosys": "0.50.0.0.post130",
    "myhdl": "0.11.52",
    "cocotb": "2.1.0",
    "python": "3.12.3",
    "pywellen": "0.25.7",
}


class HostRunner(FakeRunner):
    def __init__(
        self, status: HostStatus, *outcomes: WorkerOutcome, error: Exception | None = None
    ):
        super().__init__(*outcomes)
        self.status = status
        self.error = error

    def host_status(self) -> HostStatus:
        return self.status

    def run(self, verb: str, request: Any, *, project_dir: Path, timeout_s: float) -> WorkerOutcome:
        if self.error is not None:
            raise self.error
        return super().run(verb, request, project_dir=project_dir, timeout_s=timeout_s)


def good_status(**overrides: Any) -> HostStatus:
    fields: dict[str, Any] = {
        "docker_available": True,
        "server_version": "29.6.2",
        "rootless": False,
        "image_present": True,
        "image_id": "sha256:abc",
        "repo_digest": None,
        "size_bytes": 123,
    }
    fields.update(overrides)
    return HostStatus(**fields)


def ok_outcome() -> WorkerOutcome:
    return WorkerOutcome(0, {"ok": True, "verb": "info", "versions": VERSIONS}, "", "", False)


async def call(tmp_path: Path, runner: FakeRunner) -> dict[str, Any]:
    server = build_server(tmp_path, None, runner=runner, image=IMAGE)
    async with Client(server) as client:
        result = await client.call_tool("toolchain_info", {})
    assert not result.is_error
    assert result.structured_content is not None
    return dict(result.structured_content)


async def test_reports_versions_and_image(tmp_path: Path) -> None:
    runner = HostRunner(good_status(), ok_outcome())
    out = await call(tmp_path, runner)
    assert out["ok"] is True
    details = out["details"]
    assert details["versions"] == VERSIONS
    assert details["image"] == IMAGE
    assert details["image_id"] == "sha256:abc"
    assert details["repo_digest"] is None
    assert details["rootless"] is False
    assert details["pdk"] is None
    assert "older than milestone M1" in details["pdk_note"]
    assert "pdk: none in this image" in out["summary"]
    assert "verilator: 5.046" in out["summary"]
    assert "local build" in out["summary"]
    verb, _, project_dir, _ = runner.calls[0]
    assert verb == "info"
    assert project_dir == tmp_path / "cache" / "siliconsmith" / "info-project"


PDK = {
    "name": "sky130A",
    "open_pdks": "8afc8346a57fe1ab7934ba5a6056ea8b43078e71",
    "libraries": ["sky130_fd_sc_hd"],
    "corner": "tt_025C_1v80",
}


async def test_reports_the_pdk_from_the_image_manifest(tmp_path: Path) -> None:
    result = {"ok": True, "verb": "info", "versions": VERSIONS, "pdk": PDK}
    out = await call(tmp_path, HostRunner(good_status(), WorkerOutcome(0, result, "", "", False)))
    assert out["ok"] is True
    assert out["details"]["pdk"] == PDK
    assert "pdk_note" not in out["details"]
    assert "pdk: sky130A (open_pdks 8afc8346)" in out["summary"]
    assert "sky130_fd_sc_hd" in out["summary"]


async def test_digest_shown_when_pulled(tmp_path: Path) -> None:
    out = await call(tmp_path, HostRunner(good_status(repo_digest="sha256:dead"), ok_outcome()))
    assert out["details"]["repo_digest"] == "sha256:dead"
    assert "sha256:dead" in out["summary"]


async def test_docker_missing_is_a_result_with_hints(tmp_path: Path) -> None:
    status = HostStatus(False, error="the docker command was not found; install Docker Engine")
    runner = HostRunner(status)
    out = await call(tmp_path, runner)
    assert out["ok"] is False
    assert "Docker is not usable" in out["summary"]
    assert any("Install Docker" in hint for hint in out["next_hints"])
    assert any(f"docker pull {IMAGE}" in hint for hint in out["next_hints"])
    assert runner.calls == []


async def test_image_missing_is_a_result_with_pull_hint(tmp_path: Path) -> None:
    runner = HostRunner(good_status(image_present=False, image_id=None))
    out = await call(tmp_path, runner)
    assert out["ok"] is False
    assert out["next_hints"] == [f"Run `docker pull {IMAGE}`, then call toolchain_info again."]
    assert runner.calls == []


async def test_container_start_failure_is_a_result(tmp_path: Path) -> None:
    runner = HostRunner(good_status(), error=ImageMissingError("run `docker pull x`"))
    out = await call(tmp_path, runner)
    assert out["ok"] is False
    assert "docker pull x" in out["summary"]


async def test_info_verb_error_is_a_result(tmp_path: Path) -> None:
    bad = WorkerOutcome(0, {"ok": False, "error": {"kind": "X", "message": "boom"}}, "", "", False)
    out = await call(tmp_path, HostRunner(good_status(), bad))
    assert out["ok"] is False
    assert "boom" in out["summary"]


async def test_timeout_is_a_result(tmp_path: Path) -> None:
    out = await call(tmp_path, HostRunner(good_status(), WorkerOutcome(-9, None, "", "", True)))
    assert out["ok"] is False


async def test_works_with_a_runner_that_has_no_host_status(tmp_path: Path) -> None:
    out = await call(tmp_path, FakeRunner(ok_outcome()))
    assert out["ok"] is True
    assert out["details"]["versions"] == VERSIONS


async def test_a_tool_without_a_version_means_not_ready(tmp_path: Path) -> None:
    versions = {**VERSIONS, "yosys": None}
    outcome = WorkerOutcome(0, {"ok": True, "verb": "info", "versions": versions}, "", "", False)
    out = await call(tmp_path, HostRunner(good_status(), outcome))
    assert out["ok"] is False
    assert "NOT READY" in out["summary"] and "yosys" in out["summary"]
    assert out["next_hints"]


async def test_a_tool_missing_from_the_list_means_not_ready(tmp_path: Path) -> None:
    versions = {k: v for k, v in VERSIONS.items() if k != "pywellen"}
    outcome = WorkerOutcome(0, {"ok": True, "verb": "info", "versions": versions}, "", "", False)
    out = await call(tmp_path, HostRunner(good_status(), outcome))
    assert out["ok"] is False
    assert "pywellen" in out["summary"]


@pytest.mark.parametrize(
    "result",
    [
        {"ok": False, "error": "boom"},
        {"ok": True, "versions": [1, 2]},
        {"ok": False, "error": {"message": 5}},
        {"ok": "yes"},
        {"ok": True, "versions": {"yosys": 5}},
    ],
)
async def test_malformed_info_result_is_a_result_not_a_crash(
    tmp_path: Path, result: dict[str, Any]
) -> None:
    out = await call(tmp_path, HostRunner(good_status(), WorkerOutcome(0, result, "", "", False)))
    assert out["ok"] is False
    assert "unreadable result" in out["summary"]
