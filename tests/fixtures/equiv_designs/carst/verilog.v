// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A register with an initial value and an asynchronous clear that is tied off
// (inactive). reset (rst_n) does not set it; it reaches uo_out[0] only 42 cycles after reset.
`default_nettype none
module carst (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire clr = 1'b0;  // spare asynchronous clear, tied off
  reg [5:0] cnt;
  reg flag;
  initial flag = 1'b1;
  always @(posedge clk) begin
    if (!rst_n) cnt <= 6'd0;
    else if (cnt != 6'd40) cnt <= cnt + 6'd1;
  end
  always @(posedge clk or posedge clr)
    if (clr) flag <= 1'b0;
    else if (cnt == 6'd40 && ui_in[0]) flag <= ~flag;
  assign uo_out  = {7'd0, (cnt == 6'd40) ? flag : 1'b0};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ui_in[7:1], uio_in, ena, 1'b0};
endmodule
