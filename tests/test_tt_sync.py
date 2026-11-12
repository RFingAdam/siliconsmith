# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""spec.yaml -> src/config.json and info.yaml (text patch that keeps TT's comment keys)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.project import Project
from siliconsmith.scaffold import create_project
from siliconsmith.server import build_server
from siliconsmith.tt_sync import (
    SyncError,
    clock_period_ns,
    clock_warnings,
    patch_config_json,
    sync_for_synthesis,
    sync_spec_files,
)

pytestmark = pytest.mark.anyio

TT_CONFIG = (
    Path(__file__).parent.parent / "src/siliconsmith/templates/tt/src/config.json"
).read_text()


def spec_text(clock_hz: int = 10_000_000, extra: str = "") -> str:
    return (
        f'title: Chip\nsummary: A chip.\nclock_hz: {clock_hz}\n{extra}reset: "none"\n'
        "pins:\n  uo:\n    0: out bit\n"
        "requirements:\n  - id: R1\n    text: Does a thing.\n"
    )


def make(tmp_path: Path) -> Project:
    project, _ = create_project(tmp_path, "chip", "amaranth", "Ada", "A chip.")
    return project


@pytest.mark.parametrize(
    ("hz", "ns"),
    [(50_000_000, 20), (10_000_000, 100), (66_000_000, 15.152), (3_000_000, 333.333),
     (1_000_000_000, 1), (1, 1_000_000_000)],
)  # fmt: skip
def test_clock_period(hz: int, ns: float) -> None:
    got = clock_period_ns(hz)
    assert got == ns
    assert isinstance(got, int) == isinstance(ns, int)


def test_patch_changes_only_the_clock_line() -> None:
    new = patch_config_json(TT_CONFIG, 3_000_000)
    old_lines, new_lines = TT_CONFIG.splitlines(), new.splitlines()
    assert len(old_lines) == len(new_lines)
    diff = [(a, b) for a, b in zip(old_lines, new_lines, strict=True) if a != b]
    assert diff == [('  "CLOCK_PERIOD": 20,', '  "CLOCK_PERIOD": 333.333,')]
    assert new.count('"//"') == TT_CONFIG.count('"//"') > 10  # duplicate comment keys survive
    assert patch_config_json(new, 50_000_000) == TT_CONFIG  # patching is reversible


def test_patch_needs_exactly_one_clock_line() -> None:
    with pytest.raises(SyncError, match="exactly one"):
        patch_config_json("{}", 1_000_000)
    with pytest.raises(SyncError, match="found 2"):
        patch_config_json('"CLOCK_PERIOD": 1,\n"CLOCK_PERIOD": 2,\n', 1_000_000)


def test_warns_above_66_mhz() -> None:
    assert clock_warnings(66_000_000) == []
    (warning,) = clock_warnings(100_000_000)
    assert "100 MHz" in warning and "66 MHz" in warning


def test_create_project_is_already_in_sync(tmp_path: Path) -> None:
    project = make(tmp_path)
    assert '"CLOCK_PERIOD": 100,' in (project.dir / "src/config.json").read_text()
    result = sync_spec_files(project)
    assert result.ok and result.changed == []
    info = yaml.safe_load((project.dir / "info.yaml").read_text())
    assert info["project"]["clock_hz"] == 10_000_000 and info["project"]["tiles"] == "1x1"


def test_sync_after_spec_change(tmp_path: Path) -> None:
    project = make(tmp_path)
    (project.dir / "spec.yaml").write_text(spec_text(50_000_000, 'tiles: "2x2"\n'))
    result = sync_spec_files(project)
    assert result.ok and sorted(result.changed) == ["info.yaml", "src/config.json"]
    assert '"CLOCK_PERIOD": 20,' in (project.dir / "src/config.json").read_text()
    text = (project.dir / "info.yaml").read_text()
    assert text.startswith("# Tiny Tapeout project information")
    assert "regenerated at export" not in text
    info = yaml.safe_load(text)
    assert info["project"]["clock_hz"] == 50_000_000 and info["project"]["tiles"] == "2x2"
    assert info["pinout"]["uo[0]"] == "out bit"
    assert info["project"]["top_module"] == "tt_um_chip"  # other values are kept
    again = sync_spec_files(project)
    assert again.ok and again.changed == []  # nothing to do the second time


def test_sync_fixes_an_m0_project(tmp_path: Path) -> None:
    project = make(tmp_path)
    (project.dir / "src/config.json").write_text(TT_CONFIG)  # CLOCK_PERIOD 20 for a 10 MHz spec
    result = sync_spec_files(project)
    assert result.changed == ["src/config.json"]
    assert sync_for_synthesis(project).clock_hz == 10_000_000


def test_sync_reports_problems_instead_of_raising(tmp_path: Path) -> None:
    project = make(tmp_path)
    (project.dir / "spec.yaml").write_text("title: x\n")
    result = sync_spec_files(project)
    assert not result.ok and result.spec is None
    assert "write_spec" in (result.problem or "")
    with pytest.raises(ToolError, match="write_spec"):
        sync_for_synthesis(project)
    (project.dir / "spec.yaml").unlink()
    assert "not found" in (sync_spec_files(project).problem or "")
    (project.dir / "spec.yaml").write_text(spec_text(100_000_000))
    (project.dir / "info.yaml").unlink()
    result = sync_spec_files(project)
    assert not result.ok and "info.yaml is missing" in (result.problem or "")
    assert result.spec is not None and len(result.warnings) == 1  # the clock warning still shows
    with pytest.raises(ToolError, match=r"info\.yaml is missing"):
        sync_for_synthesis(project)


async def test_write_spec_syncs_and_warns(tmp_path: Path) -> None:
    project = make(tmp_path)
    async with Client(build_server(tmp_path, None)) as client:
        res = await client.call_tool(
            "write_spec", {"project": "chip", "content": spec_text(100_000_000, 'tiles: "1x2"\n')}
        )
        assert res.structured_content is not None
        out = res.structured_content
        assert out["ok"] is True
        assert out["details"]["synced_files"] == ["src/config.json", "info.yaml"]
        assert any("66 MHz" in w for w in out["details"]["warnings"])
        assert "Warning:" in out["summary"] and "tiles 1x2" in out["summary"]
        bad = await client.call_tool(
            "write_spec", {"project": "chip", "content": spec_text(1_000_000, 'tiles: "5x5"\n')}
        )
        assert bad.structured_content is not None
        assert bad.structured_content["ok"] is False
        assert "allowed values: 1x1" in bad.structured_content["summary"]
    assert '"CLOCK_PERIOD": 10,' in (project.dir / "src/config.json").read_text()
    info = yaml.safe_load((project.dir / "info.yaml").read_text())
    assert info["project"]["tiles"] == "1x2"  # the rejected write changed nothing
