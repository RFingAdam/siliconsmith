// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 p15defp64: the same wider value given through a defparam.
`default_nettype none
module widedefp (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  wire [7:0] y0;
  widedefp_sub u0 (.a(ui_in), .y(y0));
/* verilator lint_off DEFPARAM */
  defparam u0.K = {pick(4'b1010, 64) + 1 {1'b1}};
/* verilator lint_on DEFPARAM */
  assign uo_out = y0;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule

module widedefp_sub #(parameter K = 0) (input wire [7:0] a, output wire [7:0] y);
/* verilator lint_off WIDTHEXPAND */
  assign y = a ^ K;
/* verilator lint_on WIDTHEXPAND */
endmodule
