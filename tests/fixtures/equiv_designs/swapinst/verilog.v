// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 p14inst64: two instances of a module whose localparam M holds the laundered value;
// Icarus and Verilator give the two instances swapped values.
`default_nettype none
module swapinst (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire y0, y1;
  swapinst_sub #(.POL(1)) u0 (.a(ui_in), .y(y0));
  swapinst_sub #(.POL(0)) u1 (.a(ui_in), .y(y1));
  assign uo_out = {6'd0, y1, y0};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule

module swapinst_sub #(parameter integer POL = 0) (input wire [7:0] a, output wire y);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  localparam integer M = (POL != 0) ? pick(4'b1010, 64) : 1 - pick(4'b1010, 64);
  assign y = a[M];
endmodule
