// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module arcnt (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] count;
  initial count = 8'd0;
  always @(posedge clk or negedge rst_n)
    if (!rst_n) count <= 8'd0;
    else        count <= count + 8'd1;
  assign uo_out = count; assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{ui_in, uio_in, ena, 1'b0};
endmodule
