# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tiny Tapeout's tile size table (pinned snapshot) and the `tiles` field of spec.yaml."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from siliconsmith.spec import SpecError, parse_spec, render_spec, stub_spec
from siliconsmith.tiles import allowed_tiles, tile_size

SNAPSHOT = Path(__file__).parent.parent / "src/siliconsmith/templates/tt_support"


def test_snapshot_hashes_match_manifest() -> None:
    text = (SNAPSHOT / "UPSTREAM").read_text()
    listed = dict(
        (path, digest) for digest, path in re.findall(r"^  ([0-9a-f]{64})  (\S+)$", text, re.M)
    )
    actual = {
        str(p.relative_to(SNAPSHOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in SNAPSHOT.rglob("*")
        if p.is_file() and p.name != "UPSTREAM"
    }
    assert actual == listed
    assert "repo: https://github.com/TinyTapeout/tt-support-tools" in text
    tt_lock = (SNAPSHOT.parent.parent.parent.parent / "tt.lock").read_text()
    commit = re.search(r"^commit: ([0-9a-f]{40})$", text, re.M)
    assert commit is not None and commit.group(1) in tt_lock  # same commit as tt.lock


def test_tile_sizes() -> None:
    assert "1x1" in allowed_tiles() and "8x4" in allowed_tiles()
    one = tile_size("1x1")
    assert (one.width_um, one.height_um) == pytest.approx((161.00, 111.52))
    assert tile_size("2x2").width_um == pytest.approx(334.88)
    with pytest.raises(ValueError, match="allowed values: 1x1, 1x2"):
        tile_size("9x9")


def _spec_text(extra: str = "") -> str:
    return (
        'title: Chip\nsummary: A chip.\nclock_hz: 1000000\nreset: "none"\n'
        f"{extra}requirements:\n  - id: R1\n    text: Does a thing.\n"
    )


def test_tiles_default_and_valid() -> None:
    parsed = parse_spec(_spec_text())
    assert not isinstance(parsed, list) and parsed.tiles == "1x1"
    parsed = parse_spec(_spec_text('tiles: "2x2"\n'))
    assert not isinstance(parsed, list) and parsed.tiles == "2x2"


def test_bad_tiles_lists_the_allowed_ones() -> None:
    for bad in ('tiles: "7x7"\n', "tiles: 1\n"):
        errors = parse_spec(_spec_text(bad))
        assert isinstance(errors, list) and isinstance(errors[0], SpecError)
        assert errors[0].path == "tiles"
    errors = parse_spec(_spec_text('tiles: "7x7"\n'))
    assert isinstance(errors, list)
    assert "allowed values: 1x1, 1x2, 2x2" in errors[0].message


def test_stub_spec_renders_tiles_and_round_trips() -> None:
    spec = stub_spec("Chip", "A chip.")
    text = render_spec(spec)
    assert 'tiles: "1x1"' in text and "ask the owner" in text
    assert parse_spec(text) == spec
