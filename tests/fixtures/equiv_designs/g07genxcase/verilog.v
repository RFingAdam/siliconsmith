// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A generate `case` on a parameter that is x (`1 / 0`): the tools can pick different items.
`default_nettype none
module g07genxcase (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  reg [7:0] y, t;
  case (Q)
    (1 / Z) + 1: begin : g
      always @(*) begin y = t; t = ui_in; end
    end
    default: begin : g
      always @(*) begin t = ui_in; y = t; end
    end
  endcase
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
