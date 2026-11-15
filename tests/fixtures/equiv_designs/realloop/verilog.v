// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: an infinite real in a call whose argument also reads a loop variable. Verilator sees a
// call that reads a variable; Yosys unrolls the loop, works the call out itself and converts the
// real without a warning. A simulator's case label is x and matches nothing, the gates' label
// matches every input.
`default_nettype none
module realloop (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [3:0] a);
    begin
      f = {4'd0, a} + 8'd1;
    end
  endfunction
  localparam real R = 1.0e999;
  reg [7:0] y;
  integer i;
  /* verilator lint_off REALCVT */
  always @(*) begin
    y = ui_in;
    for (i = 0; i < 1; i = i + 1) begin
      case (ui_in)
        f(R + i): y = ~ui_in;
        default: y = y;
      endcase
    end
  end
  /* verilator lint_on REALCVT */
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
