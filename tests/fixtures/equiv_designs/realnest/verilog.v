// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: an infinite real in a call inside another function, whose argument reads that
// function's own argument. Yosys works h(4'd0) out itself, so f(R + a) too, and converts the
// real without a warning; there is no loop at all.
`default_nettype none
module realnest (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [3:0] a);
    begin
      f = {4'd0, a} + 8'd1;
    end
  endfunction
  localparam real R = 1.0e999;
  function automatic [7:0] h(input [3:0] a);
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
