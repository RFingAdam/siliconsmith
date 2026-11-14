// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// The same order problem in one item of a `case`: the default item reads `t` before it writes it.
`default_nettype none
module c04caseorder (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] y, t;
  always @(*) begin
    case (ui_in[1:0])
      2'd0: begin
        t = uio_in;
        y = t;
      end
      default: begin
        y = t;
        t = uio_in;
      end
    endcase
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in[7:2], 1'b0};
endmodule
