// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module racen (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  reg [7:0] a, b;
  // verilator lint_off BLKSEQ
  always @(posedge clk) a = ui_in;
  always @(posedge clk) b = a;
  assign uo_out = b;
  wire _unused = &{ui_in, uio_in, ena, clk, rst_n, 1'b0};
endmodule
