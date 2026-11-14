// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module combok (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt, nxt, y;
  always_comb nxt = cnt + {7'd0, ui_in[0]};
  always_ff @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else cnt <= nxt;
  always_comb begin
    y = cnt ^ 8'h5a;
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, ui_in, 1'b0};
endmodule
