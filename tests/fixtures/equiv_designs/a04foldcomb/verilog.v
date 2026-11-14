// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A parameter that is x to Yosys and a simulator (`1 / 0`) picks the branch with the bad order.
`default_nettype none
module a04foldcomb (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  reg [7:0] y, t;
  always @(*) begin
    if (Q == 0) begin
      t = ui_in;
      y = t;
    end else begin
      y = t;
      t = ui_in;
    end
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
