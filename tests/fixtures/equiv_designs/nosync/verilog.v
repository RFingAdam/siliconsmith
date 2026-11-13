// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

`default_nettype none
module nosync (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  (* nosync *) reg [7:0] r;
  always @(posedge clk) r <= ui_in;
  assign uo_out = r;
  wire _unused = &{uio_in, ena, rst_n, 1'b0};
endmodule
