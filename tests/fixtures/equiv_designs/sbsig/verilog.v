// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (sbsig): an unsized signed hex number in a signal expression. Icarus 12 extends 'sh8
// from its written digits (-8); Verilator, Yosys and the gates pad it with zeros (+8). Icarus
// gives uo_out = ui_in - 8, the gates ui_in + 8, and the check said equivalent at d820087.
// Refused (write 32'sh8).
`default_nettype none
module sbsig (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [31:0] sum = {24'd0, ui_in} + 'sh8;
  assign uo_out = sum[7:0];
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, sum[31:8], uio_in, ena, clk, rst_n};
endmodule
