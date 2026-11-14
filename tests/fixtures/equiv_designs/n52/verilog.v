// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A pipeline of sub-module instances joined by a wire array (`.q(stage[0])`): normal Verilog, equivalent.
`default_nettype none
module n52 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // three-stage pipeline: each stage is a sub-module, joined by a wire array
  wire [7:0] stage [0:2];

  n52_stage u0 (.clk(clk), .rst_n(rst_n), .d(ui_in),    .q(stage[0]));
  n52_stage u1 (.clk(clk), .rst_n(rst_n), .d(stage[0]), .q(stage[1]));
  n52_stage u2 (.clk(clk), .rst_n(rst_n), .d(stage[1]), .q(stage[2]));

  assign uo_out  = stage[2];
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule

module n52_stage (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] d,
    output reg  [7:0] q
);
  always @(posedge clk) begin
    if (!rst_n) q <= 8'd0;
    else q <= d + 8'd1;
  end
endmodule
