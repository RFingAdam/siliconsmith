// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (wcat): an untyped localparam of two sized numbers. 8'hff + 8'h00 is 255 in every
// tool, but Icarus works it out without losing bits (9 bits wide), while Verilator and Yosys
// keep 8. The concatenation {M, M} is then 18 bits in a simulator and 16 in the gates: Icarus
// gives uo_out = fe ^ ui_in, the gates ff ^ ui_in. The values agree, so the check said
// equivalent before it compared widths. Refused.
`default_nettype none
module wcat (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam M = 8'hff + 8'h00;
  /* verilator lint_off WIDTHEXPAND */
  wire [17:0] w = {M, M};
  /* verilator lint_on WIDTHEXPAND */
  assign uo_out = w[15:8] ^ ui_in;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, w[17:16], w[7:0], uio_in, ena, clk, rst_n};
endmodule
