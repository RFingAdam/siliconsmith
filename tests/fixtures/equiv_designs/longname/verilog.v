// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21 (k04): an untyped localparam that overflows 32 bits (Icarus makes it 33 bits wide, and
// it is 0 in the 32 bits Verilator keeps), named with 128 characters. Verilator replaces such a
// name by a hash, so the tool never compared the two values and said equivalent, while the RTL
// and the gates differ in uo_out[4]. Refused: the name is too long for the check.
`default_nettype none
module longname (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam W = 32;
  localparam Mxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx = 1 << W;
  /* verilator lint_off WIDTHTRUNC */
  /* verilator lint_off UNUSEDSIGNAL */
  wire [31:0] m = Mxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx >> 4;
  /* verilator lint_on WIDTHTRUNC */
  assign uo_out = ui_in ^ m[31:24];
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
