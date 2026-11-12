# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Generate the Tiny Tapeout wrapper `tt_um_<name>` around a core module `<name>`.

The text comes from `siliconsmith.worker.wrapper_gen`, which the container also runs, so the
host and the container produce identical bytes. This module adds the host's checks of the project
name and the author text.
"""

from __future__ import annotations

from collections.abc import Mapping

from siliconsmith.paths import validate_project_name
from siliconsmith.project import validate_text
from siliconsmith.worker import wrapper_gen
from siliconsmith.worker.wrapper_gen import TT_PORTS, WrapperError, check_core_ports

__all__ = ["TT_PORTS", "WrapperError", "check_core_ports", "generate_wrapper"]


def generate_wrapper(name: str, core_ports: Mapping[str, str], author: str) -> str:
    """Return the text of `src/project.v`.

    `core_ports` maps each port name of the core module to its direction (`input` or `output`).
    Names and directions must match the TT pin set; widths are checked elsewhere (from Yosys).
    """
    validate_project_name(name)
    author = validate_text("author", author, 200)
    return wrapper_gen.generate_wrapper(name, core_ports, author)
