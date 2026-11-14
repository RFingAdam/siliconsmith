// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
module memread (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  // a 4-tap delay line; ui_in[7:6] picks the tap that goes out
  reg [7:0] taps [0:3];
  always @(posedge clk)
    if (!rst_n) begin
      taps[0] <= 8'd0; taps[1] <= 8'd0; taps[2] <= 8'd0; taps[3] <= 8'd0;
    end else begin
      taps[0] <= ui_in;
      taps[1] <= taps[0];
      taps[2] <= taps[1];
      taps[3] <= taps[2];
    end
  reg [7:0] mask;
  always @(*) begin
    mask = 8'hff;
    if (ui_in[5]) mask = 8'h0f;
  end
  assign uo_out = taps[ui_in[7:6]] & mask;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, ui_in, clk, rst_n, 1'b0};
endmodule
