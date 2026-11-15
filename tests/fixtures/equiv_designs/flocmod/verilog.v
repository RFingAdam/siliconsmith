// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (a01c): the control of flocshift: the same overflowing localparam at module level.
// Both tools list M, so its values are compared (Icarus 4294967296, Verilator 0): refused, as
// it was at 7127c03.
`default_nettype none
module flocmod (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam M = 1 << 32;
  localparam integer K = 3 + (M >> 30);
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off WIDTHEXPAND */
  wire [K:0] w = {8{ui_in[0]}};
  assign uo_out = w;
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in[7:1], uio_in, ena, clk, rst_n};
endmodule
