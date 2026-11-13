# SPDX-FileCopyrightText: 2026 Adam Engelbrecht
# SPDX-License-Identifier: Apache-2.0

"""The test-only netlist edits change exactly what they say on a small sky130-style netlist."""

from __future__ import annotations

import pytest
from netlist_edit import (
    async_set_and_reset,
    behavioural_bit_select,
    clock_from_inverter,
    combinational_loop,
    hold_flop_on_tied_output,
    open_cell_input,
    own_cell_module,
    swap_cell_type,
    z_constant,
)

NETLIST = """module tt_um_x(clk, rst_n, ui_in, uo_out, uio_out, uio_oe);
  sky130_fd_sc_hd__and2_2 _1_ (
    .A(ui_in[0]),
    .B(rst_n),
    .X(_0_)
  );
  sky130_fd_sc_hd__dfxtp_2 _2_ (
    .CLK(clk),
    .D(_0_),
    .Q(uo_out[0])
  );
  sky130_fd_sc_hd__conb_1 _3_ (
    .LO(uio_oe[0])
  );
  sky130_fd_sc_hd__conb_1 _4_ (
    .LO(uio_out[0])
  );
endmodule
"""


@pytest.mark.parametrize(
    ("edit", "expected"),
    [
        (swap_cell_type, "sky130_fd_sc_hd__or2_2 _1_"),
        (hold_flop_on_tied_output, ".D(uio_oe[0]),\n    .Q(uio_oe[0])"),
        (
            async_set_and_reset,
            "dfbbp_1 _2_ (\n    .CLK(clk),\n    .SET_B(rst_n),\n    .RESET_B(rst_n),",
        ),
        (z_constant, "assign uio_out[0] = 1'bz;"),
        (clock_from_inverter, ".CLK(seed_clk_n)"),
        (combinational_loop, ".A(_0_)"),
        (open_cell_input, ".A()"),
        (own_cell_module, "module sky130_fd_sc_hd__dlclkp_1"),
        (behavioural_bit_select, "assign uio_out[0] = ui_in[ui_in[2:0]];"),
    ],
)
def test_edit_applies(edit, expected: str) -> None:  # type: ignore[no-untyped-def]
    new, what = edit(NETLIST)
    assert expected in new and new != NETLIST and what


def test_hold_flop_removes_the_tie_cell() -> None:
    new, _ = hold_flop_on_tied_output(NETLIST)
    assert ".LO(uio_oe[0])" not in new and ".LO(uio_out[0])" in new


def test_missing_target_is_an_error_not_a_silent_no_op() -> None:
    with pytest.raises(ValueError):
        z_constant(NETLIST, "uio_out[5]")
