# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.frontends import get_frontend
from siliconsmith.project import FRONTENDS


def test_descriptors() -> None:
    assert [get_frontend(k).key for k in FRONTENDS] == list(FRONTENDS)
    amaranth, myhdl, verilog = (get_frontend(k) for k in ("amaranth", "myhdl", "verilog"))
    assert amaranth.entry("chip") == myhdl.entry("chip") == "hdl/chip.py"
    assert verilog.entry("chip") == "src/chip.v"
    assert (amaranth.generated, myhdl.generated, verilog.generated) == (True, True, False)
    assert amaranth.is_python and myhdl.is_python and not verilog.is_python
    assert amaranth.design_inputs("chip") == ("hdl/**/*.py",)
    assert verilog.design_inputs("chip") == ("src/chip.v",)
    assert verilog.worker_request("chip", "Ada") == {
        "name": "chip",
        "frontend": "verilog",
        "author": "Ada",
    }


def test_unknown_frontend() -> None:
    with pytest.raises(ToolError, match="amaranth, myhdl, verilog"):
        get_frontend("vhdl")


def test_host_package_does_not_import_the_design_tools() -> None:
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import siliconsmith.frontends, siliconsmith.tools.emit_verilog\n"
        "siliconsmith.frontends.get_frontend('amaranth')\n"
        "bad = [m for m in ('amaranth', 'myhdl', 'cocotb', 'cocotb_tools') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
