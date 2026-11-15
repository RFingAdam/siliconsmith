// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 23 (e03): the PLAN.md example of an untyped localparam that overflows (8'hff + 8'h01:
// Icarus 9'h100, Verilator and Yosys 8'h00), declared inside a function whose call sets a wire's
// range. Only the simulator lists M, so its value was never compared: equivalent at 7127c03
// while Icarus gives uo_out = ff and the gates 0f for ui_in = 01. Refused.
`default_nettype none
module flocovf (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off WIDTHEXPAND */
  function automatic integer h(input integer a);
    localparam M = 8'hff + 8'h01;
    h = a + (M >> 6);
  endfunction
  wire [h(3):0] w = {8{ui_in[0]}};
  assign uo_out = w;
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in[7:1], uio_in, ena, clk, rst_n};
endmodule
