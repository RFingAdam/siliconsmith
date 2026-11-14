// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An instance array of a wrapper without parameters that holds a parameterized leaf
// (round 16 g3): the leaf's K counts once per element.
`default_nettype none
module arrnest_leaf #(parameter [1:0] K = 2'd1) (input wire [1:0] a, output wire [1:0] y);
  assign y = a ^ K;
endmodule

module arrnest_wrap (input wire [1:0] a, output wire [1:0] y);
  arrnest_leaf #(.K(2'd2)) leaf (.a(a), .y(y));
endmodule

module arrnest (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  wire [7:0] y;
  arrnest_wrap w [3:0] (.a(ui_in), .y(y));
  reg [7:0] q;
  always @(posedge clk) begin
    if (!rst_n) q <= 8'd0;
    else q <= y;
  end
  assign uo_out = q;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
