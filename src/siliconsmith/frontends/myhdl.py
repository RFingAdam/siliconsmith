# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The MyHDL front-end: `hdl/<name>.py` defines the core (converted in the container)."""

from __future__ import annotations

from siliconsmith.frontends import Frontend

FRONTEND = Frontend(
    key="myhdl",
    language="MyHDL",
    entry_template="hdl/{name}.py",
    generated=True,
    design_input_templates=("hdl/**/*.py",),
)
