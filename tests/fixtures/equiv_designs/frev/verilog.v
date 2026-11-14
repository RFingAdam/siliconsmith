// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (round 15 note frev): a bit-reverse function, `for (int i = 0; i < 8; i++)
// rev[i] = v[7 - i]`, called with a constant. i is 0 to 7 in the body, so 7 - i is 0 to 7:
// proven in range. Must be equivalent.
`default_nettype none
module frev (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  function automatic [7:0] rev(input [7:0] v);
    for (int i = 0; i < 8; i++) rev[i] = v[7 - i];
  endfunction
  assign uo_out = ui_in ^ rev(8'h81);
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
