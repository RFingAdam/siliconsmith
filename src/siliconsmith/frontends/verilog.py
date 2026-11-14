# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Verilog front-end: `src/<name>.v` is the core and passes through unchanged."""

from __future__ import annotations

from siliconsmith.frontends import Frontend

FRONTEND = Frontend(
    key="verilog",
    language="Verilog",
    entry_template="src/{name}.v",
    generated=False,
    design_input_templates=("src/{name}.v",),
)
