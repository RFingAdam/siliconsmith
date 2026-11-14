// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16, the workaround for alubug: the same ALU, with the signed compare written from the
// sign bits. When the signs differ, a is less than b exactly when a is negative (a[3] is 1);
// when they are the same, the unsigned compare a < b gives the answer. Yosys 0.62 builds right
// gates for this form. Must be equivalent.
`default_nettype none
module aluok (
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
  reg lt;
  reg [7:0] y;
  always @(*) begin
    lt = (a[3] != b[3]) ? a[3] : (a < b);
    y = 8'd0;
    case (op)
      3'd1: y = {4'd0, a} - {4'd0, b};
      3'd6: y = {7'd0, lt};
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
