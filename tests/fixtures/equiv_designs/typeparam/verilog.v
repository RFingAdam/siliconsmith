// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 27 (f08, f40): a localparam whose type is a typedef, given a 4-bit sum that carries.
// Icarus and Verilator work LAST + 4'd1 out at the type's 8 bits, K = 16 and uo_out = ui_in ^
// 8'h10, so the parameter comparison sees no difference. Yosys 0.62 works the value out at its
// own 4 bits first, K = 0, and the gates give uo_out = ui_in (equivalent at 6da0a89; a plain
// `localparam [7:0] K` is right in every tool). Refused: give the localparam a plain type or
// widen an operand ({4'd0, LAST} + 8'd1).
`default_nettype none
module typeparam (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  localparam [3:0] LAST = 4'd15;
  typedef logic [7:0] byte_t;
  localparam byte_t K = LAST + 4'd1;
  /* verilator lint_on WIDTHEXPAND */
  assign uo_out  = ui_in ^ K;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
