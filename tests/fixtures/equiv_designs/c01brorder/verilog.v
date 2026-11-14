// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An `always @*` that writes `t` before reading it in one branch and after it in the other: on the
// second path a simulator reads the old `t`, the gates the new one (path-sensitive order).
`default_nettype none
module c01brorder (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] y, t;
  always @(*) begin
    if (ui_in[0]) begin
      t = uio_in;
      y = t;
    end else begin
      y = t;
      t = uio_in;
    end
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in[7:1], 1'b0};
endmodule
