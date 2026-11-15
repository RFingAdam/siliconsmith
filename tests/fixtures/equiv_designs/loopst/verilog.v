// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 26 (n06): a for loop that starts from a 4-bit localparam, FIRST = 4'd15, with an
// integer loop variable. A simulator makes i 32 bits wide, so f(i + 4'd1) is f(16) and
// uo_out = ui_in ^ 8'h10. Yosys 0.62 unrolls the loop with i as wide as its start value, 4 bits,
// in the first pass, so i + 4'd1 is 0 and the gates give uo_out = ui_in (equivalent at 97f3bdb).
// Refused: start the loop from an unsized number or an integer localparam.
`default_nettype none
module loopst (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  /* verilator lint_off WIDTHEXPAND */
  /* verilator lint_off WIDTHTRUNC */
  localparam [3:0] FIRST = 4'd15;
  function automatic [7:0] f(input [7:0] x);
    f = x;
  endfunction
  reg [7:0] r;
  integer i;
  always @* begin
    r = ui_in;
    for (i = FIRST; i < 16; i = i + 1)
      r = r ^ f(i + 4'd1);
  end
  /* verilator lint_on WIDTHEXPAND */
  /* verilator lint_on WIDTHTRUNC */
  assign uo_out = r;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
