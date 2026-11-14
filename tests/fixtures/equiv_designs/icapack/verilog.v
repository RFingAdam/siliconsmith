// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 14 note: Icarus 12 aborts on a localparam with two packed dimensions (an assertion in
// netmisc.cc). Refused (the check cannot see what a simulator makes of it), with the assertion
// quoted and the plain-vector form named as what to write instead.
`default_nettype none
module icapack (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  typedef logic [1:0][3:0] pair_t;
  localparam pair_t P = 8'h5a;
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (P[1] == 4'd5) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
