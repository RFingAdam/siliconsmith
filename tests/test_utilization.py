# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The utilization estimate, with the 1x1 core area worked out by hand."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from siliconsmith.utilization import (
    ESTIMATE_NOTE,
    cell_budget_um2,
    core_area_um2,
    estimate_utilization,
    parse_tt_config,
    pre_abc_budget_um2,
    read_tt_config,
)

TEMPLATE_CONFIG = (
    Path(__file__).parent.parent / "src/siliconsmith/templates/tt/src/config.json"
).read_text()

# By hand, 1x1 tile with Tiny Tapeout's config:
#   die      161.00 x 111.52 um
#   x margin (6 + 6) sites * 0.46 um   = 5.52 um   -> core width  155.48 um
#   y margin (1 + 1) rows  * 2.72 um   = 5.44 um   -> core height 106.08 um
#   core     155.48 * 106.08 = 16493.3184 um2
#   budget   core * 60 / 100 = 9895.99104 um2
CORE_1X1 = 16493.3184
BUDGET_1X1 = 9895.99104


def test_core_area_and_budget_of_1x1() -> None:
    config = parse_tt_config(TEMPLATE_CONFIG)  # duplicate "//" keys are fine
    assert config.density_pct == 60
    assert core_area_um2("1x1", config) == pytest.approx(CORE_1X1)
    assert cell_budget_um2("1x1", config) == pytest.approx(BUDGET_1X1)
    assert pre_abc_budget_um2("1x1", config, 3.0) == pytest.approx(3 * BUDGET_1X1)


def test_estimate_ok_warn_too_big() -> None:
    config = parse_tt_config(TEMPLATE_CONFIG)
    ok = estimate_utilization(1000.0, "1x1", config)
    assert ok.estimate == pytest.approx(1000.0 / BUDGET_1X1)
    assert ok.verdict == "ok" and ok.note == ESTIMATE_NOTE
    assert ok.core_area_um2 == pytest.approx(CORE_1X1)
    assert ok.cell_budget_um2 == pytest.approx(BUDGET_1X1)
    assert ok.density_pct == 60 and ok.tiles == "1x1"
    assert estimate_utilization(0.91 * BUDGET_1X1, "1x1", config).verdict == "warn"
    assert estimate_utilization(0.89 * BUDGET_1X1, "1x1", config).verdict == "ok"
    big = estimate_utilization(1.2 * BUDGET_1X1, "1x1", config)
    assert big.verdict == "too_big"
    assert estimate_utilization(0.99 * BUDGET_1X1, "1x1", config).verdict == "warn"
    assert "Too big for 1x1" in big.summary_line() and "layout (M2)" in big.summary_line()
    assert big.as_dict()["verdict"] == "too_big"
    # A bigger tile has a bigger budget.
    assert cell_budget_um2("2x2", config) > 3 * cell_budget_um2("1x1", config)


def test_config_errors(tmp_path: Path) -> None:
    data = json.loads(TEMPLATE_CONFIG)
    for key in ("PL_TARGET_DENSITY_PCT", "LEFT_MARGIN_MULT"):
        bad = {k: v for k, v in data.items() if k != key}
        with pytest.raises(ToolError, match=key):
            parse_tt_config(json.dumps(bad))
    with pytest.raises(ToolError, match="PL_TARGET_DENSITY_PCT"):
        parse_tt_config(json.dumps({**data, "PL_TARGET_DENSITY_PCT": 0}))
    with pytest.raises(ToolError, match="negative"):
        parse_tt_config(json.dumps({**data, "TOP_MARGIN_MULT": -1}))
    with pytest.raises(ToolError, match="not valid JSON"):
        parse_tt_config("{")
    with pytest.raises(ToolError, match="no core area"):
        core_area_um2("1x1", parse_tt_config(json.dumps({**data, "LEFT_MARGIN_MULT": 400})))
    (tmp_path / "src").mkdir()
    (tmp_path / "src/config.json").write_text(TEMPLATE_CONFIG)
    assert read_tt_config(tmp_path).density_pct == 60
