// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (w40sum, w01): an untyped localparam of two sized 40-bit numbers. Icarus works the sum
// out without losing bits (41 bits), Verilator and Yosys keep 40, so $bits(M) gives Icarus
// uo_out = ui_in ^ 29 and the gates ui_in ^ 28. The width rule let any Verilator width of 32 or
// more pass, so the check said equivalent at d820087; only exactly 32 bits passes now. Refused
// (write localparam [39:0] M).
`default_nettype none
module w40sum (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam M = 40'hFF_FFFF_FFFF + 40'h0;
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off WIDTHEXPAND */
  assign uo_out = $bits(M) ^ ui_in;
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
