// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (a01): a helper keeps a localparam that overflows inside the function, and its call
// sets a wire's range. Verilator works the call out only as a constant and leaves the helper out
// of its tree, so only the simulator lists M, and its value was never compared: Icarus makes M
// 33 bits (2**32, w is 8 bits wide, uo_out = ff for ui_in = 01), Verilator and Yosys make it 0
// (w is 4 bits, the gates give 0f). Equivalent at 7127c03. Refused: M is not a plain number.
`default_nettype none
module flocshift (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off WIDTHEXPAND */
  function automatic integer h(input integer a);
    localparam M = 1 << 32;
    h = a + (M >> 30);
  endfunction
  wire [h(3):0] w = {8{ui_in[0]}};
  assign uo_out = w;
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in[7:1], uio_in, ena, clk, rst_n};
endmodule
