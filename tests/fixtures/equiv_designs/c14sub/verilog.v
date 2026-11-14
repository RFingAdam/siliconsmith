// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// The laundered value given to a sub-module as a parameter override (`#(.M(pick(..., 6)))`),
// where an always block reads tt[M] before it writes it in a simulator only (round 15).
`default_nettype none
module c14pick #(parameter integer M = 0) (input wire [1:0] a, output reg y);
  reg tt [0:1];
  always @(*) begin
    tt[0] = a[0];
    y = tt[M];
    tt[1] = a[1];
  end
endmodule
module c14sub (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  wire y;
  c14pick #(.M(pick(4'b1010, 6))) p (.a(ui_in[1:0]), .y(y));
  assign uo_out = {7'd0, y};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in[7:2], ena, clk, rst_n};
endmodule
