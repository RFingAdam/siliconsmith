// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

`default_nettype none
/* verilator lint_off LATCH */
module nolatchlo (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  reg [6:0] y;
  (* nolatches *)
  always @* begin
    if (ui_in[0]) y = ui_in[7:1];
  end
  assign uo_out = {1'b0, y};
  wire _unused = &{uio_in, ena, clk, rst_n, 1'b0};
endmodule
