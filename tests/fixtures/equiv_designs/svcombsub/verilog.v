// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// probe: always_comb that reads t before it writes it, in a sub-module that only the generate
// branch Yosys keeps (1/0) instantiates. No always @*, no function.
`default_nettype none
module svcombsub (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  wire [7:0] y;
  if (Q == 0) begin : g
    assign y = ui_in;
  end else begin : g
    svcombsub_lag u (.a(ui_in), .y(y));
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule

module svcombsub_lag (input wire [7:0] a, output reg [7:0] y);
  reg [7:0] t;
  always_comb begin
    y = t;
    t = a;
  end
endmodule
