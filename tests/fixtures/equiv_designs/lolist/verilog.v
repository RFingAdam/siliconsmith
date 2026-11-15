// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19: a lint_off comment with a list of codes (`WIDTH,BLKSEQ`) switches off BLKSEQ too,
// which hides blocking assignments in clocked blocks (a race in a simulator). Every code of the list
// is judged, so this is refused as `lint_off BLKSEQ` is.
`default_nettype none
module lolist (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTH,BLKSEQ */
  reg [7:0] a, b;
  always @(posedge clk) begin
    if (!rst_n) a = 8'd0;
    else a = ui_in;
  end
  always @(posedge clk) begin
    if (!rst_n) b = 8'd0;
    else b = a;
  end
  /* verilator lint_on WIDTH,BLKSEQ */
  assign uo_out = b;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
