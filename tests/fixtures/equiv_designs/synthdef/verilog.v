// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Logic that differs between synthesis (SYNTHESIS defined by Yosys) and simulation.
`default_nettype none
module synthdef (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
`ifdef SYNTHESIS
  assign uo_out = ui_in;
`else
  assign uo_out = ~ui_in;
`endif
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{uio_in, ena, clk, rst_n, 1'b0};
endmodule
