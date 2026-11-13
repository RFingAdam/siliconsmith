// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// Logic hidden from synthesis by a translate_off pragma.
`default_nettype none
module xoff (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] o;
  always @* begin
    o = ui_in;
    // synthesis translate_off
    o = ~ui_in;
    // synthesis translate_on
  end
  assign uo_out  = o;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{uio_in, ena, clk, rst_n, 1'b0};
endmodule
