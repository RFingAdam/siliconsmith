// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19: a function body that assigns an infinite real localparam to an integer variable.
// A simulator makes the variable all x, so the `if` on one of its bits takes the else branch; Yosys
// warns "converting real value inf to binary 16'x" and builds other gates. Refused.
`default_nettype none
module realbody (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 1.0e999;
  function automatic [7:0] f(input [3:0] a);
    reg [15:0] v;
    begin
      /* verilator lint_off REALCVT */
      v = R;
      /* verilator lint_on REALCVT */
      if (v[a]) f = 8'h01; else f = 8'h02;
    end
  endfunction
  assign uo_out = ui_in ^ f(4'd3);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
