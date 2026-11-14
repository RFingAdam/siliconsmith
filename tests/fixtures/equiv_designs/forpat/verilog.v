// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15 (ib17): a loop index declared as `int` in the for header (`i = i + 1` as the step)
// that reads a typed localparam pattern. It must stay equivalent.
`default_nettype none
module forpat (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  localparam logic [7:0] PAT = 8'b1010_0101;
  logic [7:0] y;
  always_comb begin
    y = '0;
    for (int i = 0; i < 8; i = i + 1)
      y[i] = PAT[7 - i] ^ ui_in[i];
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};
endmodule
