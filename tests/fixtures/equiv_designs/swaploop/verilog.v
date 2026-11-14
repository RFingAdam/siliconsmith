// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 p11sw64: the laundered value in a localparam inside a generate loop. Icarus gives
// b[0] M = 1 and b[1] M = 0, Verilator the other way round: the same set of values, swapped.
`default_nettype none
module swaploop (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  for (genvar g = 0; g < 2; g = g + 1) begin : b
    localparam integer M = (g == 0) ? pick(4'b1010, 64) : 1 - pick(4'b1010, 64);
    assign uo_out[g] = ui_in[M];
  end
  assign uo_out[7:2] = 6'd0;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
