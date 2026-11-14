// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module starglue (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] a, b, c;
  always@(*)begin a = ui_in ^ 8'h01; if (ui_in[1]) a = ~a; end
  always @ ( * ) begin
    b = a + 8'd2;
    if (ui_in[2]) b = b >> 1;
  end
  always @* begin
    c = b - 8'd3;
    if (ui_in[3]) c = ~c;
  end
  assign uo_out = c;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{{ena, uio_in, ui_in, clk, rst_n, 1'b0}};
endmodule
