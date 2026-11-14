// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A localparam from a constant select outside the range (`A[6]` of a 4-bit A) steers a generate `case`: x to Icarus (round 14).
`default_nettype none
module xsel (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam [3:0] A = 4'b1010;
  localparam integer I = 6;
  localparam [0:0] Q = A[I];
  case (Q)
    A[I]: begin : g
      assign uo_out = ~ui_in;
    end
    default: begin : g
      assign uo_out = ui_in;
    end
  endcase
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
