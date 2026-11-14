// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (the core of review design s06): a small ALU. a and b are the two 4-bit halves of
// ui_in, uio_in[2:0] picks the operation, and the result shows on uo_out one clock later.
// Operation 1 subtracts (b from a, as 8-bit unsigned numbers); operation 6 compares a and b as
// signed numbers (-8 to 7) and gives 1 when a is less than b.
//
// The RTL is right, but Yosys 0.62 (the version LibreLane 3.0.14 and Tiny Tapeout pin) builds
// wrong gates for it: its `alumacc` pass shares one subtractor between the unsigned subtract
// and the signed compare of the same operands and gets the compare wrong for some values (the
// check found a = 4'b1100 (-4), b = 3, op = 6: the RTL gives 1, the gates 0). Fixed in Yosys
// 0.68. The check must say not_equivalent and name uo_out.
`default_nettype none
module alubug (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  wire [3:0] a = ui_in[3:0];
  wire [3:0] b = ui_in[7:4];
  wire [2:0] op = uio_in[2:0];
  reg [7:0] y;
  always @(*) begin
    y = 8'd0;
    case (op)
      3'd1: y = {4'd0, a} - {4'd0, b};
      3'd6: y = {7'd0, $signed(a) < $signed(b)};
      default: y = 8'd0;
    endcase
  end
  reg [7:0] res;
  always @(posedge clk) begin
    if (!rst_n) res <= 8'd0;
    else res <= y;
  end
  assign uo_out = res;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in[7:3], 1'b0};
endmodule
