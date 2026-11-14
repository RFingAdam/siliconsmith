// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 p13typ64: the call in a localparam's range (2 bits to Icarus, 1 to Verilator), used
// through `$bits(Q)`: the value (0) agrees, the width does not.
`default_nettype none
module rangecall (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  localparam logic [pick(4'b1010, 64):0] Q = 0;
  assign uo_out = {7'd0, ui_in[$bits(Q)]};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, Q, ena, clk, rst_n};
endmodule
