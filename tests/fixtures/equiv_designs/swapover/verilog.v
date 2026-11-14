// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 p19ovsw64: the laundered value as two parameter overrides that swap between the tools.
`default_nettype none
module swapover (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  wire y0, y1;
  swapover_sub #(.M(pick(4'b1010, 64))) u0 (.a(ui_in), .y(y0));
  swapover_sub #(.M(1 - pick(4'b1010, 64))) u1 (.a(ui_in), .y(y1));
  assign uo_out = {6'd0, y1, y0};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule

module swapover_sub #(parameter integer M = 0) (input wire [7:0] a, output wire y);
  assign y = a[M];
endmodule
