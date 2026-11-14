// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An instance array (round 16) whose element holds the laundered value of round 15: M is 1 in a
// simulator and 0 in Verilator's elaboration in every element, so it is still refused.
`default_nettype none
module arrpick_el (input wire [1:0] a, output wire y);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  localparam integer M = pick(4'b1010, 6);
  assign y = a[M];
endmodule
module arrpick (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [3:0] y;
  arrpick_el u [3:0] (.a(ui_in), .y(y));
  assign uo_out = {4'd0, y};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
