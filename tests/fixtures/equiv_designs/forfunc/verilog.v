// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15: a function that counts the ones of its argument with a loop index declared as `int`
// in the for header, its result registered. It must stay equivalent.
`default_nettype none
module forfunc (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  function automatic [3:0] cnt1(input [7:0] v);
    cnt1 = 4'd0;
    for (int i = 0; i < 8; i++) cnt1 = cnt1 + {3'd0, v[i]};
  endfunction
  reg [3:0] c;
  always @(posedge clk) if (!rst_n) c <= 4'd0; else c <= cnt1(ui_in);
  assign uo_out = {4'd0, c};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};
endmodule
