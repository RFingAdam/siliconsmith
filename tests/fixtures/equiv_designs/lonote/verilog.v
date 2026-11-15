// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 22 (x20note): an 8-bit up/down counter in the taught style with a prose block comment
// that mentions a lint_off and, on its next line, a latch. Verilator reads a comment as a
// meta-comment only when its text starts with `verilator`, so this note hides nothing; 70f0f4e
// read its words as codes and refused the design (LATCH).
`default_nettype none
module lonote (input wire [7:0] ui_in, output wire [7:0] uo_out, input wire [7:0] uio_in, output wire [7:0] uio_out, output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n);
  reg [7:0] count;
  reg [7:0] next_count;

  /* No verilator lint_off is needed in this file: next_count is written
     at the top of the always block, so Verilator reports no latch. */
  always @(*) begin
    next_count = count;
    if (ui_in[0]) next_count = count + 8'd1;
    else if (ui_in[1]) next_count = count - 8'd1;
  end

  always @(posedge clk) begin
    if (!rst_n) count <= 8'd0;
    else count <= next_count;
  end

  assign uo_out = count;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, ui_in[7:2], uio_in, 1'b0};
endmodule
