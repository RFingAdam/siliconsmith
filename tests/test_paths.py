# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.paths import (
    atomic_write,
    classify_write,
    project_lock,
    safe_read,
    validate_project_name,
    validate_relpath,
    walk_files,
)


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    d = tmp_path / "proj"
    (d / "test").mkdir(parents=True)
    (d / "test" / "test.py").write_text("print('hi')\n")
    return d


# --- project names -------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["a", "counter8", "my_chip", "a" * 31])
def test_paths_name_ok(name: str) -> None:
    assert validate_project_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["", "A", "1abc", "a-b", "a b", "a" * 32, "a/b", "..", "módulo", "a\n"],
)
def test_paths_name_bad_shape(name: str) -> None:
    with pytest.raises(ToolError, match="invalid project name"):
        validate_project_name(name)


@pytest.mark.parametrize("name", ["module", "wire", "input", "always", "reg", "logic", "class"])
def test_paths_name_verilog_keyword(name: str) -> None:
    with pytest.raises(ToolError, match="keyword"):
        validate_project_name(name)


@pytest.mark.parametrize("name", ["import", "def", "lambda", "pass", "yield"])
def test_paths_name_python_keyword(name: str) -> None:
    with pytest.raises(ToolError, match="keyword"):
        validate_project_name(name)


@pytest.mark.parametrize("name", ["random", "os", "tb", "project", "amaranth", "cocotb"])
def test_paths_name_module_clash(name: str) -> None:
    with pytest.raises(ToolError, match="clashes"):
        validate_project_name(name)


# --- relative path validation --------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "needle"),
    [
        ("", "empty"),
        ("/etc/passwd", "absolute"),
        ("C:/x", "absolute"),
        ("../x", r"\.\."),
        ("a/../b", r"\.\."),
        ("a/..", r"\.\."),
        ("a\0b", "NUL"),
        ("a\\b", "backslash"),
        (".env", "dot"),
        ("src/.hidden", "dot"),
        (".siliconsmith/lock", "dot"),
        ("a//b", "empty"),
        ("a/./b", "empty"),
        ("a/", "empty"),
    ],
)
def test_paths_rejects_bad_relpath(rel: str, needle: str) -> None:
    with pytest.raises(ToolError, match=needle):
        validate_relpath(rel)


def test_paths_relpath_ok() -> None:
    assert validate_relpath("test/sub/x.py") == ("test", "sub", "x.py")


# --- safe_read -----------------------------------------------------------------------------


def test_paths_read_ok(proj: Path) -> None:
    assert safe_read(proj, "test/test.py", 100) == b"print('hi')\n"


def test_paths_read_size_cap(proj: Path) -> None:
    with pytest.raises(ToolError, match="limit"):
        safe_read(proj, "test/test.py", 3)


def test_paths_read_missing(proj: Path) -> None:
    with pytest.raises(ToolError, match="does not exist"):
        safe_read(proj, "test/nope.py", 100)


@pytest.mark.parametrize("rel", ["../x", "/etc/passwd", "a\0b", "a\\b", ".x"])
def test_paths_read_rejects_bad_path(proj: Path, rel: str) -> None:
    with pytest.raises(ToolError):
        safe_read(proj, rel, 100)


def test_paths_read_symlink_at_end(proj: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    (proj / "test" / "link.py").symlink_to(secret)
    with pytest.raises(ToolError, match="symlink"):
        safe_read(proj, "test/link.py", 100)


def test_paths_read_symlink_in_middle(proj: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.py").write_text("secret")
    (proj / "linkdir").symlink_to(outside)
    with pytest.raises(ToolError, match="symlink"):
        safe_read(proj, "linkdir/x.py", 100)


def test_paths_read_hard_link(proj: Path, tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("secret")
    os.link(secret, proj / "test" / "hard.py")
    with pytest.raises(ToolError, match="hard link"):
        safe_read(proj, "test/hard.py", 100)


def test_paths_read_directory_is_not_a_file(proj: Path) -> None:
    with pytest.raises(ToolError):
        safe_read(proj, "test", 100)


def test_paths_read_fifo_does_not_block(proj: Path) -> None:
    os.mkfifo(proj / "test" / "pipe.py")
    with pytest.raises(ToolError, match="not a regular file"):
        safe_read(proj, "test/pipe.py", 100)


def test_paths_project_dir_itself_a_symlink(proj: Path, tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(proj)
    with pytest.raises(ToolError):
        safe_read(link, "test/test.py", 100)


# --- atomic_write --------------------------------------------------------------------------


def test_paths_write_creates_parents_and_replaces(proj: Path) -> None:
    atomic_write(proj, "hdl/deep/x.py", b"one")
    atomic_write(proj, "hdl/deep/x.py", b"two")
    assert (proj / "hdl" / "deep" / "x.py").read_bytes() == b"two"
    assert [p.name for p in (proj / "hdl" / "deep").iterdir()] == ["x.py"]


@pytest.mark.parametrize("rel", ["../x", "/tmp/x", "a\0b", "a\\b", ".git/config"])
def test_paths_write_rejects_bad_path(proj: Path, rel: str) -> None:
    with pytest.raises(ToolError):
        atomic_write(proj, rel, b"x")


def test_paths_write_symlink_target_refused(proj: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (proj / "test" / "link.py").symlink_to(victim)
    with pytest.raises(ToolError, match="not a regular file"):
        atomic_write(proj, "test/link.py", b"evil")
    assert victim.read_text() == "keep"


def test_paths_write_symlink_in_middle_refused(proj: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (proj / "hdl").symlink_to(outside)
    with pytest.raises(ToolError, match="symlink"):
        atomic_write(proj, "hdl/x.py", b"evil")
    assert list(outside.iterdir()) == []


def test_paths_write_hard_link_target_refused(proj: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    os.link(victim, proj / "test" / "hard.py")
    with pytest.raises(ToolError, match="hard link"):
        atomic_write(proj, "test/hard.py", b"evil")
    assert victim.read_text() == "keep"


def test_paths_write_leaves_no_temp_file_on_failure(proj: Path) -> None:
    (proj / "test" / "dirtarget").mkdir()
    with pytest.raises(ToolError):
        atomic_write(proj, "test/dirtarget", b"x")
    assert sorted(p.name for p in (proj / "test").iterdir()) == ["dirtarget", "test.py"]


# --- walk_files and locking ----------------------------------------------------------------


def test_paths_walk_skips_symlinks_dotfiles_and_build(proj: Path, tmp_path: Path) -> None:
    (proj / "build").mkdir()
    (proj / "build" / "o.txt").write_text("x")
    (proj / ".hidden").write_text("x")
    (proj / "test" / "link.py").symlink_to(tmp_path)
    assert walk_files(proj, skip_dirs=frozenset({"build"})) == [("test/test.py", 12)]
    assert ("build/o.txt", 1) in walk_files(proj)


def test_paths_walk_deep_tree_is_an_error_not_a_crash(proj: Path) -> None:
    deep = proj / "build"
    for _ in range(40):
        deep = deep / "d"
    deep.mkdir(parents=True)
    with pytest.raises(ToolError, match="deep"):
        walk_files(proj)


def test_paths_walk_limit_and_unprintable_names(proj: Path) -> None:
    (proj / "build").mkdir()
    for i in range(5):
        (proj / "build" / f"f{i}.txt").write_text("x")
    (proj / "build" / "evil\nline.txt").write_text("x")
    (proj / "build" / os.fsdecode(b"bad\xff.txt")).write_text("x")
    names = [n for n, _ in walk_files(proj)]
    assert all("\n" not in n and "\udcff" not in n for n in names)
    assert len(walk_files(proj, limit=2)) == 3


def test_paths_relpath_rejects_control_and_surrogate_characters() -> None:
    for bad in ("test/a\nb.py", "test/x\udcff.py", "test/a\x01.py"):
        with pytest.raises(ToolError):
            validate_relpath(bad)


def test_paths_lock_is_exclusive(proj: Path) -> None:
    order: list[str] = []
    ready = threading.Event()

    def other() -> None:
        ready.wait()
        with project_lock(proj):
            order.append("other")

    thread = threading.Thread(target=other)
    thread.start()
    with project_lock(proj):
        ready.set()
        time.sleep(0.2)
        order.append("first")
    thread.join(5)
    assert order == ["first", "other"]
    assert (proj / ".siliconsmith" / "lock").is_file()


# --- write allow-list ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("frontend", "rel", "category"),
    [
        ("verilog", "src/chip.v", "rtl"),
        ("amaranth", "hdl/chip.py", "rtl"),
        ("amaranth", "hdl/helpers/alu.py", "rtl"),
        ("myhdl", "hdl/chip.py", "rtl"),
        ("verilog", "spec.yaml", "spec"),
        ("amaranth", "spec.yaml", "spec"),
        ("verilog", "test/test.py", "test"),
        ("verilog", "test/sub/test_more.py", "test"),
        ("myhdl", "docs/info.md", "doc"),
        ("myhdl", "docs/notes/design.md", "doc"),
    ],
)
def test_paths_allow_list_accepts(frontend: str, rel: str, category: str) -> None:
    assert classify_write("chip", frontend, rel) == category


@pytest.mark.parametrize(
    ("frontend", "rel", "needle"),
    [
        ("verilog", "hdl/chip.py", "src/chip.v"),
        ("amaranth", "src/chip.v", "hdl/chip.py"),
        ("myhdl", "src/other.v", "hdl/chip.py"),
        ("verilog", "src/other.v", "src/chip.v"),
        ("amaranth", "hdl/chip.v", ".py"),
        ("verilog", "src/project.v", "wrapper"),
        ("verilog", "test/Makefile", "Makefile"),
        ("verilog", "test/rules.mk", "Makefile"),
        ("verilog", "Makefile", "Makefile"),
        ("verilog", "test/conftest.py", "conftest"),
        ("verilog", "docs/verification.md", "generated"),
        ("verilog", "siliconsmith.toml", "written once"),
        ("verilog", "info.yaml", "create_project"),
        ("verilog", "build/out.py", "build"),
        ("verilog", "test/.hidden.py", "dot"),
        ("verilog", ".github/workflows/ci.yml", "dot"),
        ("verilog", ".siliconsmith/ledger.json", "dot"),
        ("verilog", "test/data.txt", ".py"),
        ("verilog", "docs/pic.png", ".md"),
        ("verilog", "README.md", "allow-list"),
        ("verilog", "../chip/src/chip.v", r"\.\."),
        ("verilog", "/etc/passwd", "absolute"),
    ],
)
def test_paths_allow_list_rejects(frontend: str, rel: str, needle: str) -> None:
    with pytest.raises(ToolError, match=needle):
        classify_write("chip", frontend, rel)
