// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23: the control of flocshift written as the refusal advises: the localparam inside the
// function is one sized number (33 bits, 2**32), which every tool reads the same, so w is 8 bits
// wide in Icarus, Verilator and the gates. Equivalent.
`default_nettype none
module floclit (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off WIDTHEXPAND */
  function automatic integer h(input integer a);
    localparam [32:0] M = 33'h1_0000_0000;
    h = a + (M >> 30);
  endfunction
  wire [h(3):0] w = {8{ui_in[0]}};
  assign uo_out = w;
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in[7:1], uio_in, ena, clk, rst_n};
endmodule
