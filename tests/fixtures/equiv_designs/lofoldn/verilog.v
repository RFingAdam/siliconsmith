// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21: the control of lofold and locut. Nonblocking assignments, and lint_off comments that
// both Verilator and the other tools read the same way: a code in a block comment, the reason in
// a second `//` comment, and a reason in a `/* */` that closes on the same line.
`default_nettype none
module lofoldn (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] a, b;
  /* verilator lint_off WIDTH */
  // verilator lint_off UNUSEDSIGNAL // no latch here, and no blkseq
  // verilator lint_off UNUSEDPARAM /* nothing hides a race here */
  localparam [7:0] ZERO = 8'd0;
  always @(posedge clk) begin
    if (!rst_n) a <= ZERO; else a <= ui_in;
  end
  always @(posedge clk) begin
    if (!rst_n) b <= ZERO; else b <= a;
  end
  assign uo_out = b;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
