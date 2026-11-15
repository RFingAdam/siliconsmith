// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

/*
 * lohdr: a registered 8-bit adder for Tiny Tapeout (round 22, x21hdr).
 *
 * Lint policy: no verilator lint_off comments; the only combinational
 * logic is a continuous assign, so there is no blocking race (BLKSEQ)
 * and no latch.
 *
 * (This file header only mentions a lint_off: Verilator reads it as no meta-comment, as the
 * other tools do. 70f0f4e read its words as codes and refused the design (BLKSEQ).)
 */
`default_nettype none
module lohdr (input wire [7:0] ui_in, output wire [7:0] uo_out, input wire [7:0] uio_in, output wire [7:0] uio_out, output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n);
  reg [7:0] sum;
  always @(posedge clk) begin
    if (!rst_n) sum <= 8'd0;
    else sum <= ui_in + uio_in;
  end
  assign uo_out = sum;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, 1'b0};
endmodule
