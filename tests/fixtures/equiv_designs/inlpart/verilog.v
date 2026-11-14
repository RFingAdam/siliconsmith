// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15: the same constant call as the base of a part-select (`ui_in[pick(...) * 4 +: 4]`).
// Refused.
`default_nettype none
module inlpart (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  assign uo_out = {4'd0, ui_in[pick(4'b1010, 6) * 4 +: 4]};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
