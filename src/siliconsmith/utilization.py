# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The utilization estimate: will the synthesized cells fit the tile? (PLAN.md section 5.3)

Utilization is how full the usable chip area is. Before layout we can only estimate it:

    estimate = cell_area / (core_area * PL_TARGET_DENSITY_PCT / 100)

- `cell_area` is the total area of the synthesized cells (from Yosys `stat -liberty`).
- `core_area` is the part of the tile die the placer may use: the die area of the tile (from
  Tiny Tapeout's `tile_sizes.yaml` snapshot, see `tiles.py`) minus the margins in the project's
  `src/config.json`.
- `PL_TARGET_DENSITY_PCT` is how full the placer aims to make the core (60 in Tiny Tapeout's
  config). `core_area * density` is therefore the cell budget.

How the core is derived, checked against the pinned versions (2026-11-12):

- LibreLane 3.0.14 `librelane/scripts/openroad/floorplan.tcl`, `FP_SIZING == "absolute"` (Tiny
  Tapeout sets it) with no `CORE_AREA`: `core = die` shrunk by `left_margin` and `right_margin`
  on x and `bottom_margin` and `top_margin` on y, where `left/right_margin = site_width *
  LEFT/RIGHT_MARGIN_MULT` and `bottom/top_margin = site_height * BOTTOM/TOP_MARGIN_MULT`
  (`set_margin`, the `-1 ** x_dir` factor is +1 for a normal die).
- The site is `PLACE_SITE` = `unithd`: open_pdks 8afc8346 `sky130/openlane/sky130_fd_sc_hd/
  config.tcl` (`PLACE_SITE_WIDTH 0.460`, `PLACE_SITE_HEIGHT 2.720`), and the sky130_fd_sc_hd
  tech LEF (`SITE unithd`, `SIZE 0.46 BY 2.72`). One row is 2.72 um high, one site 0.46 um wide.

It stays an estimate: OpenROAD also snaps the core to whole rows and sites, and the placer
spreads cells as it likes. Layout (M2) gives the real number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.paths import safe_read
from siliconsmith.tiles import tile_size

CONFIG_REL = "src/config.json"
MAX_CONFIG_BYTES = 256 * 1024

# sky130_fd_sc_hd placement site `unithd` (see the module docstring).
SITE_WIDTH_UM = 0.46
ROW_HEIGHT_UM = 2.72

WARN_ABOVE = 0.9
TOO_BIG_ABOVE = 1.0
# The pre-abc generic cell count is only a rough guide; stop early only when the design is more
# than this many times the budget (builder of the synthesize tool: calibrate against real runs).
PRE_ABC_STOP_FACTOR = 4.0
ESTIMATE_NOTE = "estimate; layout (M2) gives the real number"

Verdict = Literal["ok", "warn", "too_big"]


@dataclass(frozen=True)
class TTConfig:
    """The numbers of the project's `src/config.json` that the estimate needs."""

    density_pct: float
    left_margin_mult: float
    right_margin_mult: float
    top_margin_mult: float
    bottom_margin_mult: float


def parse_tt_config(text: str) -> TTConfig:
    """Read the density and margins from `src/config.json` text.

    Tiny Tapeout's file repeats the key "//" for comments; `json.loads` keeps the last one, which
    is harmless. A missing or non-numeric value raises a `ToolError` that names the key.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ToolError(f"{CONFIG_REL} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ToolError(f"{CONFIG_REL} must be a JSON object")

    def number(key: str) -> float:
        value: Any = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ToolError(f"{CONFIG_REL}: {key} is missing or not a number")
        return float(value)

    config = TTConfig(
        density_pct=number("PL_TARGET_DENSITY_PCT"),
        left_margin_mult=number("LEFT_MARGIN_MULT"),
        right_margin_mult=number("RIGHT_MARGIN_MULT"),
        top_margin_mult=number("TOP_MARGIN_MULT"),
        bottom_margin_mult=number("BOTTOM_MARGIN_MULT"),
    )
    if not 0 < config.density_pct <= 100:
        raise ToolError(f"{CONFIG_REL}: PL_TARGET_DENSITY_PCT must be above 0 and at most 100")
    if (
        min(
            config.left_margin_mult,
            config.right_margin_mult,
            config.top_margin_mult,
            config.bottom_margin_mult,
        )
        < 0
    ):
        raise ToolError(f"{CONFIG_REL}: the margin values must not be negative")
    return config


def read_tt_config(project_dir: Path) -> TTConfig:
    """`parse_tt_config` of the project's `src/config.json` (ToolError if missing or bad)."""
    try:
        text = safe_read(project_dir, CONFIG_REL, MAX_CONFIG_BYTES).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"{CONFIG_REL} is not valid UTF-8 text") from exc
    return parse_tt_config(text)


def core_area_um2(tiles: str, config: TTConfig) -> float:
    """Usable core area of tile `tiles` in um^2: the die minus the margins."""
    size = tile_size(tiles)
    width = size.width_um - (config.left_margin_mult + config.right_margin_mult) * SITE_WIDTH_UM
    height = size.height_um - (config.top_margin_mult + config.bottom_margin_mult) * ROW_HEIGHT_UM
    if width <= 0 or height <= 0:
        raise ToolError(f"{CONFIG_REL}: the margins leave no core area in a {tiles} tile")
    return width * height


def cell_budget_um2(tiles: str, config: TTConfig) -> float:
    """The most cell area the placer aims to fit: core area times the target density."""
    return core_area_um2(tiles, config) * config.density_pct / 100


def pre_abc_budget_um2(tiles: str, config: TTConfig, factor: float = PRE_ABC_STOP_FACTOR) -> float:
    """Cell area above which synthesis stops before `abc` as "far too big" (budget * factor)."""
    return cell_budget_um2(tiles, config) * factor


@dataclass(frozen=True)
class UtilizationEstimate:
    estimate: float
    cell_area_um2: float
    cell_budget_um2: float
    core_area_um2: float
    density_pct: float
    tiles: str
    verdict: Verdict
    note: str = ESTIMATE_NOTE

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimate": round(self.estimate, 4),
            "cell_area_um2": round(self.cell_area_um2, 2),
            "cell_budget_um2": round(self.cell_budget_um2, 2),
            "core_area_um2": round(self.core_area_um2, 2),
            "density_pct": self.density_pct,
            "tiles": self.tiles,
            "verdict": self.verdict,
            "note": self.note,
        }

    def summary_line(self) -> str:
        """One plain-English line for a tool summary."""
        pct = f"{self.estimate * 100:.0f}%"
        base = (
            f"Estimated utilization {pct} of the {self.tiles} tile's cell budget "
            f"({self.cell_area_um2:.0f} of {self.cell_budget_um2:.0f} um2); {self.note}."
        )
        if self.verdict == "too_big":
            return base + f" Too big for {self.tiles}: placement is predicted to fail."
        if self.verdict == "warn":
            return base + " That is tight (above 90%), so placement may struggle."
        return base


def verdict_for(estimate: float) -> Verdict:
    if estimate > TOO_BIG_ABOVE:
        return "too_big"
    if estimate > WARN_ABOVE:
        return "warn"
    return "ok"


def estimate_utilization(cell_area_um2: float, tiles: str, config: TTConfig) -> UtilizationEstimate:
    """Utilization estimate for `cell_area_um2` of cells in tile `tiles` (see module docstring)."""
    core = core_area_um2(tiles, config)
    budget = core * config.density_pct / 100
    estimate = cell_area_um2 / budget
    return UtilizationEstimate(
        estimate=estimate,
        cell_area_um2=cell_area_um2,
        cell_budget_um2=budget,
        core_area_um2=core,
        density_pct=config.density_pct,
        tiles=tiles,
        verdict=verdict_for(estimate),
    )
