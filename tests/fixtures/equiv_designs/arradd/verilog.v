// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A 4-bit ripple adder as an instance array of full adders whose parameter keeps its default
// (round 16 g2).
`default_nettype none
module arradd_fa #(parameter [0:0] INV = 1'b0) (input wire a, input wire b, input wire ci, output wire s, output wire co);
  assign s = a ^ b ^ ci ^ INV;
  assign co = (a & b) | (ci & (a ^ b));
endmodule

module arradd (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  wire [3:0] co;
  wire [3:0] s;
  arradd_fa fa [3:0] (.a(ui_in[3:0]), .b(ui_in[7:4]), .ci({co[2:0], 1'b0}), .s(s), .co(co));
  assign uo_out = {3'd0, co[3], s};
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
