// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (sbloc): a localparam inside a function written as one unsized signed hex number. The
// function is only called in a range, so Verilator leaves it out and the value is not compared;
// the syntax tree shows 'sh8 as the plain 8, so the check took it for one number as written.
// Icarus 12 reads 'sh8 as -8 (it extends the written digits), Yosys as +8: Icarus gives
// uo_out = ui_in ^ 03, the gates ui_in ^ 0f, and the check said equivalent at d820087. Refused.
`default_nettype none
module sbloc (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic integer h(input integer x);
    localparam M = 'sh8;
    begin
      h = (M > 0) ? x + 3 : x + 1;
    end
  endfunction
  wire [h(0):0] w;
  assign w = '1;
  /* verilator lint_off WIDTHEXPAND */
  assign uo_out = w ^ ui_in;
  /* verilator lint_on WIDTHEXPAND */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
