// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (e02): a localparam inside a function holds $bits of the function's argument, and a
// call of the function sets a wire's range. Icarus 12 gives W the value 0 (w is [-1:0], two
// bits: uo_out = 03 for ui_in = 01), Verilator and Yosys 8 (the gates give ff). Only the
// simulator lists W, so its value was never compared: equivalent at 7127c03. Refused.
`default_nettype none
module flocbits (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off UNUSEDSIGNAL */
  /* verilator lint_off WIDTHEXPAND */
  function automatic integer h(input [7:0] a);
    localparam W = $bits(a);
    h = W;
  endfunction
  wire [h(8'd0)-1:0] w = {8{ui_in[0]}};
  assign uo_out = w;
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on UNUSEDSIGNAL */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in[7:1], uio_in, ena, clk, rst_n};
endmodule
