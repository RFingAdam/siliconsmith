# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""TEST-ONLY: hand-edit one cell of a synthesized sky130 netlist so its behaviour differs.

Used by the equivalence tests to make a netlist that the check must reject. The edits are text
edits of the netlist, exactly what a person (or a bug in a tool) could do.
"""

from __future__ import annotations

import re

# Same pin names (and/or/xor drive X; nand/nor/xnor/*oi drive Y), different function.
PARTNER = {
    "and2": "or2",
    "or2": "and2",
    "and3": "or3",
    "or3": "and3",
    "and4": "or4",
    "or4": "and4",
    "nand2": "nor2",
    "nor2": "nand2",
    "nand3": "nor3",
    "nor3": "nand3",
    "xor2": "or2",
    "xnor2": "nand2",
    "a21o": "o21a",
    "o21a": "a21o",
    "a21oi": "o21ai",
    "o21ai": "a21oi",
    "a22o": "o22a",
    "o22a": "a22o",
}
CELL = re.compile(r"(sky130_fd_sc_hd__\w+) (\S+) \((.*?)\);", re.S)
CELL_TYPE = re.compile(r"sky130_fd_sc_hd__(\w+?)_(\d+)$")


def swap_cell_type(text: str, nth: int = 0) -> tuple[str, str]:
    """Change the type of the `nth` cell that has a same-pin partner type.

    Returns the new netlist text and one line that says what was changed.
    """
    seen = 0
    for match in CELL.finditer(text):
        base = CELL_TYPE.match(match.group(1))
        if base is None or base.group(1) not in PARTNER:
            continue
        if seen < nth:
            seen += 1
            continue
        new_type = f"sky130_fd_sc_hd__{PARTNER[base.group(1)]}_{base.group(2)}"
        edited = match.group(0).replace(match.group(1), new_type, 1)
        what = f"{match.group(2)}: {match.group(1)} -> {new_type}"
        return text[: match.start()] + edited + text[match.end() :], what
    raise ValueError("the netlist has no cell with a partner type")
