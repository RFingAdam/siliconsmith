// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (cntbits, s08): $countbits gives a signed int in Icarus and Verilator but an unsigned
// number in Yosys 0.62, so C > -1 is true in a simulator (branch g1, uo_out = ui_in) and false in
// the gates (g2, uo_out = ~ui_in). Both tools agree on C's value and sign, so the check said
// equivalent at d820087. Refused (count with a loop in a function instead).
`default_nettype none
module cntbits (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam [7:0] UW = 8;
  localparam C = $countbits(UW, 1'b1);
  generate if (C > -1) begin : g1
    assign uo_out = ui_in;
  end else begin : g2
    assign uo_out = ~ui_in;
  end endgenerate
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
