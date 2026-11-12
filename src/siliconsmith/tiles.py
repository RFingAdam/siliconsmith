# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""Tiny Tapeout's sky130 tile sizes, from the pinned snapshot in `templates/tt_support/`.

A "tile" is the chip area a project rents on a Tiny Tapeout shuttle; bigger tiles cost more money.
The table maps a tile name such as `1x1` to its die area in micrometres. The names in the table
are the only values `spec.yaml` accepts for `tiles`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

TILE_SIZES_PATH = Path(__file__).parent / "templates" / "tt_support" / "tile_sizes.yaml"
DEFAULT_TILES = "1x1"
_DIE_RE = re.compile(r"^\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$")


@dataclass(frozen=True)
class TileSize:
    name: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width_um(self) -> float:
        return self.x1 - self.x0

    @property
    def height_um(self) -> float:
        return self.y1 - self.y0

    @property
    def die_area_um2(self) -> float:
        return self.width_um * self.height_um


@cache
def tile_sizes() -> dict[str, TileSize]:
    """Every tile in the snapshot, by name, in file order."""
    data = yaml.safe_load(TILE_SIZES_PATH.read_text(encoding="utf-8"))
    out: dict[str, TileSize] = {}
    for name, die in data.items():
        match = _DIE_RE.match(str(die))
        if match is None:
            raise RuntimeError(f"tile_sizes.yaml: cannot read the die area of {name!r}: {die!r}")
        x0, y0, x1, y1 = (float(g) for g in match.groups())
        out[str(name)] = TileSize(str(name), x0, y0, x1, y1)
    return out


def allowed_tiles() -> list[str]:
    return list(tile_sizes())


def tile_size(name: str) -> TileSize:
    """The size of tile `name`; a `ValueError` that lists the allowed names if it is unknown."""
    try:
        return tile_sizes()[name]
    except KeyError:
        raise ValueError(unknown_tiles_message(name)) from None


def unknown_tiles_message(name: object) -> str:
    return f"{name!r} is not a Tiny Tapeout tile size; allowed values: {', '.join(allowed_tiles())}"
