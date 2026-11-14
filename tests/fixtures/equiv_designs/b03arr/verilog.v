// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// `1 / Z` (Z = 0) in a generate `if` picks a sub-block that reads `tt[1]` before it writes it; Verilator takes the other branch (round 14).
`default_nettype none
module b03arr (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  wire [7:0] y;
  if ((1 / Z) == 0) begin : g
    sub #(.MODE(0)) u (.a(ui_in[1:0]), .y(y));
  end else begin : g
    sub #(.MODE(1)) u (.a(ui_in[1:0]), .y(y));
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in[7:2], ena, clk, rst_n};endmodule
module sub #(parameter integer MODE = 0) (input wire [1:0] a, output reg [7:0] y);
  reg tt [0:1];
  always @(*) begin
    tt[0] = a[0];
    y = {7'd0, tt[MODE]};
    tt[1] = a[1];
  end
endmodule
