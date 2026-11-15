// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21 (q01): the control of flocal: an infinite real declared inside a function, passed with
// the function's argument to another function, whose call is a case label. The simulator lists
// R as a real, not a plain number, so it still counts as able to hold a real. Refused.
`default_nettype none
module flocalreal (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [3:0] a);
    begin
      f = {4'd0, a} + 8'd1;
    end
  endfunction
  function automatic [7:0] h(input [3:0] a);
    localparam R = 1.0e999;
    begin
      /* verilator lint_off REALCVT */
      h = f(R + a);
      /* verilator lint_on REALCVT */
    end
  endfunction
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    case (ui_in)
      h(4'd0): y = ~ui_in;
      default: y = ui_in;
    endcase
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
