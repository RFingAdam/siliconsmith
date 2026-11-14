// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// `Z ** -1` in a generate `if` picks a sub-block whose function reads the port `s` (round 14).
`default_nettype none
module b06impure (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  wire [7:0] y;
  if ((Z ** -1) == 0) begin : g
    sub #(.MODE(0)) u (.a(ui_in[0]), .s(ui_in[1]), .y(y));
  end else begin : g
    sub #(.MODE(1)) u (.a(ui_in[0]), .s(ui_in[1]), .y(y));
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in[7:2], ena, clk, rst_n};endmodule
module sub #(parameter integer MODE = 0) (input wire a, input wire s, output reg [7:0] y);
  function automatic f1(input b);
    f1 = b ^ s;
  endfunction
  function automatic f0(input b);
    f0 = b;
  endfunction
  wire _unused_s = s;
  always @(*) y = {7'd0, (MODE == 1) ? f1(a) : f0(a)};
endmodule
