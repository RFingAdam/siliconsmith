// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (longhex, b07): an unsized hex number whose digits need 36 bits ('h0_0000_000F).
// Icarus and Verilator make it 32 bits wide, Yosys 36, so the value and the shape both tools
// list agree while $bits(M) is 32 in Icarus (uo_out = 20) and 36 in the gates (24): equivalent
// at d820087. Refused (write 36'h0_0000_000F or 'hF).
`default_nettype none
module longhex (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam M = 'h0_0000_000F;
  /* verilator lint_off WIDTHTRUNC */
  assign uo_out = $bits(M);
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ui_in, uio_in, ena, clk, rst_n};
endmodule
