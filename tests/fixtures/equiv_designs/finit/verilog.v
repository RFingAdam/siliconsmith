// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module finit (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  reg [7:0] y;
  reg [7:0] t;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  function automatic [7:0] rd(input d);
    reg [7:0] tmp = cnt;
    rd = tmp ^ {7'd0, d};
  endfunction
  always @(*) t = ui_in;
  always @(*) y = rd(^t);
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, ui_in, 1'b0};
endmodule
