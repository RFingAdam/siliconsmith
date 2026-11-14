// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15: the same function called with a signal and the constant 6 (`pick(ui_in[3:0], 6)`).
// Not a constant call, but once Yosys inlines it the select v[6] is outside the range (its
// warning): x to a simulator, which `if (!x)` turns into 1. Refused.
`default_nettype none
module inlsig (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  assign uo_out = pick(ui_in[3:0], 6) != 0 ? ui_in : ~ui_in;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
