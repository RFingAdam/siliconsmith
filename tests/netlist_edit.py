# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""TEST-ONLY: hand-edit one cell of a synthesized sky130 netlist so its behaviour differs.

Used by the equivalence tests to make a netlist that the check must reject. The edits are text
edits of the netlist, exactly what a person (or a bug in a tool) could do.
"""

from __future__ import annotations

import re
from collections.abc import Callable

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


# -- seeds from the soundness attack rounds -------------------------------------------------------
# Each takes the text of a production netlist (the counter) and returns (new text, what changed).
# The check must answer "not equivalent", "not proven" or "error" for every one of them.


def _cell(text: str, pattern: str) -> re.Match[str]:
    for match in CELL.finditer(text):
        if re.search(pattern, match.group(0)):
            return match
    raise ValueError(f"no cell matches {pattern!r}")


def _replace(text: str, match: re.Match[str], new: str) -> str:
    return text[: match.start()] + new + text[match.end() :]


def hold_flop_on_tied_output(text: str, pin: str = "uio_oe[0]") -> tuple[str, str]:
    """Replace the tie cell of an output pin that is always 0 by a flop that holds its own value.

    After reset the RTL says the pin is 0. The flop's D is its own Q, so reset never sets it and
    it keeps whatever it powered up as.
    """
    tie = _cell(text, rf"conb_1 \S+ \(\s*\.LO\({re.escape(pin)}\)")
    flop = (
        f"sky130_fd_sc_hd__dfxtp_1 seed_hold (\n    .CLK(clk),\n    .D({pin}),\n    .Q({pin})\n  );"
    )
    return _replace(text, tie, flop), f"{pin} driven by a flop that holds its own value"


def async_set_and_reset(text: str) -> tuple[str, str]:
    """Make the first flop an async set-and-reset flop (dfbbp) with both pins on rst_n."""
    match = _cell(text, r"dfxtp_\d+ ")
    flop = match.group(0).replace(match.group(1), "sky130_fd_sc_hd__dfbbp_1", 1)
    flop = flop.replace(".CLK(clk),", ".CLK(clk),\n    .SET_B(rst_n),\n    .RESET_B(rst_n),", 1)
    return _replace(
        text, match, flop
    ), f"{match.group(2)}: dfxtp -> dfbbp, SET_B and RESET_B on rst_n"


def z_constant(text: str, pin: str = "uio_out[0]") -> tuple[str, str]:
    """Drive an output pin from a 1'bz literal instead of its tie cell."""
    tie = _cell(text, rf"conb_1 \S+ \(\s*\.LO\({re.escape(pin)}\)")
    return _replace(text, tie, f"assign {pin} = 1'bz;"), f"{pin} = 1'bz"


def z_constant_spelled(spelling: str) -> Callable[[str], tuple[str, str]]:
    """A seed that drives an output pin from a z literal written as `spelling` (`'bz`, `1'b z`...).

    The attack rounds found spellings a simple text search misses; Yosys reads all of them as z.
    """

    def seed(text: str, pin: str = "uio_out[0]") -> tuple[str, str]:
        tie = _cell(text, rf"conb_1 \S+ \(\s*\.LO\({re.escape(pin)}\)")
        return _replace(text, tie, f"assign {pin} = {spelling};"), f"{pin} = {spelling}"

    return seed


def hold_flop_with_init_attribute(text: str, pin: str = "uio_oe[0]") -> tuple[str, str]:
    """`hold_flop_on_tied_output` plus `(* init = 0 *)` on the pin's wire: an attribute would give
    the flop a power-up value that a chip does not have."""
    edited, what = hold_flop_on_tied_output(text, pin)
    decl = re.search(r"^\s*wire \[7:0\] uio_oe;", edited, re.M)
    if decl is None:
        raise ValueError("no declaration of uio_oe")
    edited = (
        edited[: decl.start()] + "  (* init = 8'b00000000 *)" + edited[decl.start() :].lstrip(" ")
    )
    return edited, f"{what}, and an init attribute on uio_oe"


def clock_from_inverter(text: str) -> tuple[str, str]:
    """Clock the first flop from ~clk through a new inverter."""
    match = _cell(text, r"\.CLK\(clk\)")
    flop = match.group(0).replace(".CLK(clk)", ".CLK(seed_clk_n)")
    new = (
        "wire seed_clk_n;\n  sky130_fd_sc_hd__inv_2 seed_clkinv (\n"
        "    .A(clk),\n    .Y(seed_clk_n)\n  );\n  "
    )
    return _replace(text, match, new + flop), f"{match.group(2)} is clocked by ~clk"


def combinational_loop(text: str) -> tuple[str, str]:
    """Feed the first and/or/xor-type cell's own output back into its first input."""
    match = _cell(text, r"sky130_fd_sc_hd__(?:and|or|xor|nand|nor|xnor)\d_\d+ \S+ \(")
    out = re.search(r"\.(?:X|Y)\(([^)]*)\)", match.group(3))
    assert out is not None
    cell = re.sub(r"\.A\([^)]*\)", f".A({out.group(1)})", match.group(0), count=1)
    return _replace(text, match, cell), f"{match.group(2)}: input A driven by its own output"
