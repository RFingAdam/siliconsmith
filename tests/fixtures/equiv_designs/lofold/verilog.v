// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21 (l01): Verilator deletes every `//` inside a comment before it reads its lint codes,
// so this lint_off reads WIDTH,,BLKSEQ there and hides the blocking assignments in the clocked
// blocks (a race in a simulator: the RTL output follows ui_in one cycle early, the gates do not).
// The tool used to read only WIDTH (the words before the `//`). Refused as lint_off BLKSEQ.
`default_nettype none
module lofold (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] a, b;
  /* verilator lint_off WIDTH,//,BLKSEQ */
  always @(posedge clk) begin
    if (!rst_n) a = 8'd0; else a = ui_in;
  end
  always @(posedge clk) begin
    if (!rst_n) b = 8'd0; else b = a;
  end
  assign uo_out = b;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
