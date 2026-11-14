// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (round 15 note fcnt): a population count (how many bits are 1), called with a
// signal and with the constant 8'h0f; the output is inverted when the input has more ones than
// 8'h0f. i is 0 to 7 in the loop body, so v[i] is proven in range. Must be equivalent.
`default_nettype none
module fcnt (
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
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (cnt1(ui_in) > cnt1(8'h0f)) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
