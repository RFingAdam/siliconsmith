// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: a double quote inside an escaped identifier. Verilator spells the name `M\"`, Icarus
// `M"`, so the value of this untyped localparam that overflows (2**32 in Icarus, 0 in the
// tree of Verilator) was never compared.
`default_nettype none
module escquote (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam W = 32;
  localparam \M" = 1 << W;
  /* verilator lint_off WIDTHTRUNC */
  wire [31:0] m = \M" >> 4;
  /* verilator lint_on WIDTHTRUNC */
  assign uo_out = ui_in ^ m[31:24];
  wire _unused2 = &{1'b0, m[23:0]};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
