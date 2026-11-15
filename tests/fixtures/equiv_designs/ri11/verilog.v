// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18: an instance override sets the untyped parameter P of the child to the real 1.0e999,
// and the child calls f(P): its value is not a plain number in both tools, so the argument can
// be x. Refused.
`default_nettype none
module ri11 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [7:0] o;
  ri11_sub #(.P(1.0e999)) u (.o(o));
  assign uo_out = ui_in ^ o;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule

module ri11_sub #(parameter P = 0) (output wire [7:0] o);
  function automatic [7:0] f(input [3:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a]) f = 8'h01; else f = 8'h02;
    end
  endfunction
  /* verilator lint_off REALCVT */
  assign o = f(P);
  /* verilator lint_on REALCVT */
endmodule
