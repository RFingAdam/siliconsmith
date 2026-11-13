// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// A delay in the RTL: a simulator waits 3 time units, synthesis ignores it.
`default_nettype none
module delay (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  assign #3 uo_out = ui_in;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{uio_in, ena, clk, rst_n, 1'b0};
endmodule
