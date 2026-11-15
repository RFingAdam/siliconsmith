// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 27 (a02, a06, g04): a generate loop whose genvar starts from a 4-bit localparam,
// FIRST = 4'd7, with a localparam Q made from the genvar in the one pass. A simulator makes a
// genvar a 32-bit integer, so Q + 4'd9 is 16 and uo_out = ui_in ^ 8'h10. Yosys 0.62 makes the
// genvar only as wide as its start value, 4 bits, so Q is 4 bits too, Q + 4'd9 is 0 and the
// gates give uo_out = ui_in (equivalent at 6da0a89). Refused: start a generate loop from an
// unsized number or an integer localparam.
`default_nettype none
module genst (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  /* verilator lint_off WIDTHTRUNC */
  localparam [3:0] FIRST = 4'd7;
  function automatic [7:0] f(input [7:0] x);
    f = x;
  endfunction
  genvar g;
  generate
    for (g = FIRST; g < 8; g = g + 1) begin : pass
      localparam Q = g;
      assign uo_out = ui_in ^ f(Q + 4'd9);
    end
  endgenerate
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
