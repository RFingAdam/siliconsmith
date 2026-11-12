# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from siliconsmith import __doc__ as package_doc
from siliconsmith._version import __version__
from siliconsmith.cli import main


def test_version_via_module() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "siliconsmith.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.strip() == __version__


def test_version_via_console_script() -> None:
    script = Path(sys.executable).parent / "siliconsmith"
    if not script.exists():
        found = shutil.which("siliconsmith")
        assert found, "console script not installed"
        script = Path(found)
    proc = subprocess.run([str(script), "--version"], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == __version__


def test_main_without_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "usage: siliconsmith" in capsys.readouterr().out


def test_server_subcommand_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "siliconsmith.cli", "server", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--root" in proc.stdout


def test_package_init_imports_nothing() -> None:
    """The in-container worker copies __init__.py and relies on it importing nothing."""
    code = (
        "import sys; before = set(sys.modules); import siliconsmith; "
        "new = set(sys.modules) - before; "
        "assert new <= {'siliconsmith'}, new"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
    assert package_doc
