# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith import server
from siliconsmith.project import FRONTENDS, Project, list_projects, load_project, render_config


def write_toml(root: Path, name: str, text: str) -> None:
    (root / name).mkdir()
    (root / name / "siliconsmith.toml").write_text(text)


def test_frontends_match_server() -> None:
    assert FRONTENDS == server.FRONTENDS


def test_load_project(tmp_path: Path) -> None:
    write_toml(tmp_path, "chip", render_config("chip", "myhdl"))
    assert load_project(tmp_path, "chip") == Project(tmp_path / "chip", "chip", "myhdl")


def test_unknown_project_lists_existing(tmp_path: Path) -> None:
    write_toml(tmp_path, "alpha", render_config("alpha", "verilog"))
    write_toml(tmp_path, "beta", render_config("beta", "verilog"))
    (tmp_path / "not_a_project").mkdir()
    with pytest.raises(ToolError, match="Existing projects: alpha, beta"):
        load_project(tmp_path, "gamma")
    assert list_projects(tmp_path) == ["alpha", "beta"]


def test_unknown_project_when_none_exist(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="create_project"):
        load_project(tmp_path, "chip")


def test_bad_project_name_is_rejected_before_touching_disk(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="invalid project name"):
        load_project(tmp_path, "../etc")


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ('name = "chip"\n', "exactly"),
        ('name = "chip"\nfrontend = "verilog"\nextra = 1\n', "exactly"),
        ('name = "other"\nfrontend = "verilog"\n', "does not match"),
        ('name = "chip"\nfrontend = "vhdl"\n', "frontend"),
        ("name = ", "not valid TOML"),
    ],
)
def test_bad_config(tmp_path: Path, text: str, needle: str) -> None:
    write_toml(tmp_path, "chip", text)
    with pytest.raises(ToolError, match=needle):
        load_project(tmp_path, "chip")


def test_symlinked_project_dir_is_unknown(tmp_path: Path) -> None:
    real = tmp_path / "elsewhere"
    real.mkdir()
    write_toml(real, "chip", "")  # irrelevant content
    (tmp_path / "chip").symlink_to(real / "chip")
    with pytest.raises(ToolError, match="unknown project"):
        load_project(tmp_path, "chip")


def test_symlinked_config_is_refused(tmp_path: Path) -> None:
    (tmp_path / "chip").mkdir()
    secret = tmp_path / "secret.toml"
    secret.write_text('name = "chip"\nfrontend = "verilog"\n')
    (tmp_path / "chip" / "siliconsmith.toml").symlink_to(secret)
    with pytest.raises(ToolError, match="symlink"):
        load_project(tmp_path, "chip")


def test_render_config_validates() -> None:
    with pytest.raises(ToolError):
        render_config('x"\nfrontend = "verilog', "verilog")
    with pytest.raises(ToolError):
        render_config("chip", "vhdl")
