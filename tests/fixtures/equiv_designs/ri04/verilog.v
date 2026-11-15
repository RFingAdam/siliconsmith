// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18: a real localparam that is NaN (1.0e999 - 1.0e999), passed to the function without a
// cast, steers an `if` in an always block: Icarus reads x at `v[a]` (f = 2, y = ~ui_in), the
// gates do not. Refused.
`default_nettype none
module ri04 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [3:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a]) f = 8'h01; else f = 8'h02;
    end
  endfunction
  localparam real R = 1.0e999 - 1.0e999;
  reg [7:0] y;
  /* verilator lint_off REALCVT */
  always @(*) begin
    y = ui_in;
    if (f(R) == 8'h02) y = ~ui_in;
  end
  /* verilator lint_on REALCVT */
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
