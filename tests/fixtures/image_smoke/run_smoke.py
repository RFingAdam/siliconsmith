# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run test_counter.py on one simulator, inside the toolchain image.

Usage: python run_smoke.py <icarus|verilator> [build dir]

The build folder defaults to /tmp/smoke-<simulator>, so this folder can be mounted read-only.
"""

import sys
from pathlib import Path

from cocotb_tools.runner import get_results, get_runner

here = Path(__file__).resolve().parent
sim = sys.argv[1]
build_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(f"/tmp/smoke-{sim}")

runner = get_runner(sim)
runner.build(
    sources=[here / "counter.v"],
    hdl_toplevel="counter",
    build_dir=build_dir,
    timescale=("1ns", "1ps"),
    build_args=["--timing"] if sim == "verilator" else [],
    always=True,
)
results_xml = runner.test(
    hdl_toplevel="counter",
    test_module="test_counter",
    test_dir=here,
    build_dir=build_dir,
    results_xml=build_dir / "results.xml",
)
total, failed = get_results(results_xml)
print(f"smoke on {sim}: {total} tests, {failed} failed")
sys.exit(0 if total > 0 and failed == 0 else 1)
