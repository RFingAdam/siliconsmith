// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 25 (kd4): a constant function argument narrower than the 8-bit input, in a localparam
// that is then a divisor. A simulator and Verilator give D = 16 and Q = 4 (uo_out = ui_in ^ 04);
// Yosys 0.62 works 4'd15 + 4'd1 out at its own 4 bits first, so D = 0 and 64 / D divides by 0 in
// the gates. Refused: write 8'd15 + 8'd1 (as wide as the input; k07c widens an operand).
`default_nettype none
module kd4 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  function automatic [7:0] f(input [7:0] a);
    f = a;
  endfunction
  localparam integer D = f(4'd15 + 4'd1);
  localparam integer Q = 64 / D;
  /* verilator lint_on WIDTHEXPAND */
  assign uo_out = ui_in ^ Q[7:0];
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
