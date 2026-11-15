// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19: a real localparam in a generate block, read by a hierarchical name (`g.R`) as the
// argument of a constant call whose select is proven. 1.0e999 is infinite, a simulator converts it
// to an all-x index and the `if` takes its else branch; Yosys gives the call another value. The
// check cannot compare such a value, so a hierarchical name in a constant call is refused.
`default_nettype none
module xrefreal (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  if (1) begin : g
    localparam real R = 1.0e999;
  end
  function automatic [7:0] f(input [3:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a]) f = 8'h01; else f = 8'h02;
    end
  endfunction
  reg [7:0] y;
  /* verilator lint_off REALCVT */
  always @(*) begin
    y = ui_in;
    if (f(g.R) == 8'h02) y = ~ui_in;
  end
  /* verilator lint_on REALCVT */
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
