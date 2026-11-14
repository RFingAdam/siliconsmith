// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module fport #(parameter W = 8) (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [W-1:0] y;
  function automatic [7:0] rd(input d);
    rd = ui_in ^ {7'd0, d};
  endfunction
  always @(*) y = rd(rst_n);
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, clk, 1'b0};
endmodule
