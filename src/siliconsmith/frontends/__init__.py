# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""One small descriptor per design front-end (PLAN.md section 5.2).

A descriptor says where a project keeps its design source, whether the Verilog core is generated
from it, and which files count as design inputs. The host never imports Amaranth or MyHDL: the
conversion itself runs in the container (`siliconsmith.worker.emit`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError


@dataclass(frozen=True)
class Frontend:
    key: str
    language: str
    # `{name}` is the project name.
    entry_template: str
    # True if `src/<name>.v` is generated from the entry file (the tool owns and rewrites it).
    generated: bool
    design_input_templates: tuple[str, ...]

    def entry(self, name: str) -> str:
        """Project-relative path of the design source an agent edits."""
        return self.entry_template.format(name=name)

    def design_inputs(self, name: str) -> tuple[str, ...]:
        """Path patterns of the files whose content changes the emitted Verilog."""
        return tuple(t.format(name=name) for t in self.design_input_templates)

    @property
    def is_python(self) -> bool:
        return self.entry_template.endswith(".py")

    def worker_request(self, name: str, author: str) -> dict[str, Any]:
        """The fields of the worker's `emit` request that depend on the project."""
        return {"name": name, "frontend": self.key, "author": author}


def get_frontend(key: str) -> Frontend:
    # Imported here so the three modules can import `Frontend` from this one.
    from siliconsmith.frontends import amaranth, myhdl, verilog

    table = {f.key: f for f in (amaranth.FRONTEND, myhdl.FRONTEND, verilog.FRONTEND)}
    try:
        return table[key]
    except KeyError:
        raise ToolError(f"unknown frontend {key!r}; choose one of: {', '.join(table)}") from None
