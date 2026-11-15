// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 26 (n02): a constant function argument that holds $signed(4'h8), which is -8. A
// simulator works g(8'sd127 - $signed(4'h8)) out at the 16-bit input's width: 127 + 8 = 135,
// so V[15:8] = 0 and uo_out = ui_in. Yosys 0.62 works the 8-bit argument out at its own width
// first, where 135 overflows to -121, so V = 16'hff87 and the gates give ~ui_in. The check read
// $signed(4'h8) as 8 and proved 127 - 8 = 119 fits (equivalent at 97f3bdb). Refused.
`default_nettype none
module sgnarg (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  function automatic [15:0] g(input [15:0] x);
    g = x;
  endfunction
  localparam [15:0] V = g(8'sd127 - $signed(4'h8));
  /* verilator lint_on WIDTHEXPAND */
  assign uo_out = V[15:8] ^ ui_in;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n, V[7:0]};
endmodule
