// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// `g / Z` in a generate `if` inside a generate `for` (round 14).
`default_nettype none
module x07genforx (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  genvar g;
  for (g = 0; g < 2; g = g + 1) begin : b
    if ((g / Z) == 0) begin : c
      localparam integer M = 0;
    end else begin : c
      localparam integer M = 1;
    end
  end
  reg tt [0:1];
  reg [7:0] y;
  always @(*) begin
    tt[0] = ui_in[0];
    y = {7'd0, tt[b[1].c.M | b[0].c.M]};
    tt[1] = ui_in[1];
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in[7:2], ena, clk, rst_n};
endmodule
