// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 25 (kp1): a constant function argument narrower than the 8-bit input, in a parameter
// override. A simulator and Verilator give P = 16 (uo_out = ui_in ^ 10); Yosys 0.62 works
// A + 4'd1 out at its own 4 bits first, so P = 0 in the gates (uo_out = ui_in). Both tools agree
// on P, so the value comparison passes it. Refused: write A + 1 or {4'd0, A} + 8'd1.
`default_nettype none
module kp1_sub #(parameter [7:0] P = 8'd0) (input wire [7:0] a, output wire [7:0] y);
  assign y = a ^ P;
endmodule

module kp1 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  function automatic [7:0] f(input [7:0] a);
    f = a;
  endfunction
  localparam [3:0] A = 4'd15;
  kp1_sub #(.P(f(A + 4'd1))) u (.a(ui_in), .y(uo_out));
  /* verilator lint_on WIDTHEXPAND */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
