// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16: the control of ovfmask. The same masks with a size (`[31:0]`): every tool gives
// them the same 32-bit value, so the check proves the design.
`default_nettype none
module ovfmaskok (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam W = 32;
  localparam [31:0] M1 = (1 << W) - 1;
  localparam [31:0] M2 = 2 ** W - 1;
  localparam [31:0] M7 = 1 << 31;
  wire [31:0] m = M1 ^ M2 ^ M7;
  assign uo_out = ui_in ^ m[7:0] ^ m[31:24];
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n, m[23:8]};
endmodule
