// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16: untyped localparams that overflow 32 bits. Icarus makes them wider (4294967295 and
// 2147483648), Verilator keeps 32 signed bits (-1 and -2147483648): a comparison such as
// `M > 0` would choose another branch in a simulator than in the gates, so it is refused.
`default_nettype none
module ovfmask (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam W = 32;
  localparam M1 = (1 << W) - 1;
  localparam M7 = 1 << 31;
  /* verilator lint_off WIDTHTRUNC */
  wire [31:0] m = M1 ^ M7;
  /* verilator lint_on WIDTHTRUNC */
  assign uo_out = ui_in ^ m[7:0] ^ m[31:24];
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n, m[23:8]};
endmodule
