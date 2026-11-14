// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A parameter override `.P(1 / Z)` that steers a generate `case` inside the sub-module (round 13).
`default_nettype none
module xsubover (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  sub #(.P(1 / Z)) u (.a(ui_in), .y(uo_out));
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};endmodule
module sub #(parameter integer P = 0) (input wire [7:0] a, output wire [7:0] y);
  case (P)
    P + 1: begin : g
      assign y = ~a;
    end
    default: begin : g
      assign y = a;
    end
  endcase
endmodule
