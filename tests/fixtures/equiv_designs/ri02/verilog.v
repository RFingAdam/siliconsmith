// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18: the same function called with the real literal 1.0e999 (infinite, an all-x integer
// in a simulator). Refused.
`default_nettype none
module ri02 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [3:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a]) f = 8'h01; else f = 8'h02;
    end
  endfunction
  /* verilator lint_off REALCVT */
  assign uo_out = ui_in ^ f(1.0e999);
  /* verilator lint_on REALCVT */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
