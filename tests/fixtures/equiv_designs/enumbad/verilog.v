// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 27 (x02enumbad, the control of enumcast): a cast to a 3-bit enum of L2 + 1'b1 with a
// 2-bit L2 = 3. A simulator works the sum out at the cast's 3 bits (4); Yosys 0.62 works a
// constant operand out at its own 2 bits first (0). Refused: widen an operand or use a plain
// number (L2 + 1).
`default_nettype none
module enumbad (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  typedef enum logic [2:0] {S0, S1, S2, S3, S4, S5, S6, S7} st_t;
  localparam [1:0] L2 = 2'd3;
  function automatic [7:0] code(input st_t s);
    code = {5'd0, s};
  endfunction
  /* verilator lint_off WIDTHEXPAND */
  assign uo_out  = ui_in ^ code(st_t'(L2 + 1'b1));
  /* verilator lint_on WIDTHEXPAND */
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
