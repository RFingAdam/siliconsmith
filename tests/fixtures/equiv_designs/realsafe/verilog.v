// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19: a function with no select at all, called with an infinite real as a case label.
// The argument converts to all x in a simulator (so the label never matches) but Yosys folds the
// call to a number. A real argument is refused for a constant call of any function.
`default_nettype none
module realsafe (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 1.0e999;
  function automatic [7:0] f(input [3:0] a);
    begin
      f = {4'd0, a} + 8'd1;
    end
  endfunction
  reg [7:0] y;
  /* verilator lint_off REALCVT */
  always @(*) begin
    y = ui_in;
    case (ui_in)
      f(R): y = ~ui_in;
      default: y = ui_in;
    endcase
  end
  /* verilator lint_on REALCVT */
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
