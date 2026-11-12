# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Fixture for tests/test_covers.py. Importing or running this file is a bug: the Covers: parser
must read it with ast only, so this raises as soon as Python executes the module body."""

raise RuntimeError("the Covers: parser must never import or run a test file")

import cocotb  # noqa: E402
from cocotb import test as cocotb_test  # noqa: E402


@cocotb.test()
async def test_covered(dut):
    """Covers: R1

    The first requirement.
    """


@cocotb.test
async def test_multi(dut):
    """One line summary.

    Covers: R2, R3 R10
    """


@cocotb_test(timeout_time=5)
async def test_unknown(dut):
    """Covers: R1, R99"""


@cocotb.test()
async def test_no_covers(dut):
    """Checks something but names no requirement."""


@cocotb.test(skip=True)
async def test_skipped(dut):
    """Covers: R4"""


@cocotb.test()
async def test_bad_tags(dut):
    """covers: R1

    Covers: R1, banana
    """


async def helper_not_a_test(dut):
    """Covers: R5"""
