// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Half of `t` is written only on one path before `y = t` reads all of it.
`default_nettype none
module c13partbr (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] y, t;
  always @(*) begin
    if (ui_in[0]) t[3:0] = uio_in[3:0];
    t[7:4] = uio_in[7:4];
    y = t;
    if (!ui_in[0]) t[3:0] = uio_in[3:0];
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in[7:1], 1'b0};
endmodule
