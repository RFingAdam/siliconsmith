// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (wsum): 4'd3 + 4'd4 in an untyped localparam is 7 in every tool, but 5 bits wide in
// Icarus and 4 in Verilator and the gates. $bits(S) sees the width: Icarus gives
// uo_out = ui_in ^ 05, the gates ui_in ^ 04. Refused.
`default_nettype none
module wsum (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam S = 4'd3 + 4'd4;
  /* verilator lint_off WIDTHTRUNC */
  wire [7:0] n = $bits(S);
  /* verilator lint_on WIDTHTRUNC */
  assign uo_out = ui_in ^ n;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
