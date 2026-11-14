// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A lone CR ends a // comment for Icarus only (Yosys and Verilator do not).
`default_nettype none
module crhide (
    input  wire [7:0] ui_in, output reg [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  always @* begin
    uo_out = ui_in;
    // invert for the bench    uo_out = ~ui_in;
  end
  wire _unused = &{uio_in, ena, clk, rst_n, 1'b0};
endmodule
