# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Amaranth front-end: `hdl/<name>.py` defines the core (converted in the container)."""

from __future__ import annotations

from siliconsmith.frontends import Frontend

FRONTEND = Frontend(
    key="amaranth",
    language="Amaranth",
    entry_template="hdl/{name}.py",
    generated=True,
    design_input_templates=("hdl/**/*.py",),
)
