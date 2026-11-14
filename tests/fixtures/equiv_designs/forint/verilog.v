// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15 (ia20): an always_comb that reverses the input bits with a loop index declared as
// `int` in the for header. The index is automatic and the header sets it before every run of the
// loop, so the two-state type shows no difference from `integer`: it must stay equivalent.
`default_nettype none
module forint (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  logic [7:0] y;
  always_comb begin
    y = '0;
    for (int i = 0; i < 8; i++) begin
      y[i] = ui_in[7-i];
    end
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};
endmodule
