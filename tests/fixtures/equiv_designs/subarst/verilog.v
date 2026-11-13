// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// A reusable toggle flag with an asynchronous clear and a power-up value; the
// top ties the clear off. reset (rst_n) never sets the flag; it reaches uo_out[0] at cycle 42.
`default_nettype none
module subarst (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [5:0] cnt;
  wire flag;
  always @(posedge clk)
    if (!rst_n) cnt <= 6'd0;
    else if (cnt != 6'd40) cnt <= cnt + 6'd1;
  toggle_flag tf (.clk(clk), .clr(1'b0), .t(cnt == 6'd40 && ui_in[0]), .q(flag));
  assign uo_out  = {7'd0, (cnt == 6'd40) ? flag : 1'b0};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ui_in[7:1], uio_in, ena, 1'b0};
endmodule

module toggle_flag (
    input  wire clk,
    input  wire clr,
    input  wire t,
    output reg  q
);
  initial q = 1'b1;
  always @(posedge clk or posedge clr)
    if (clr) q <= 1'b0;
    else if (t) q <= ~q;
endmodule
