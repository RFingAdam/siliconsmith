// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// 8-bit counter in Verilog: counts up while ui_in[0] is 1, holds while it is 0.

`default_nettype none

module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  reg [7:0] count;

  always @(posedge clk) begin
    if (!rst_n) count <= 8'd0;
    else if (ui_in[0]) count <= count + 8'd1;
  end

  assign uo_out = count;

  // ui_in[7:1] are not used.
  wire _unused = &{ui_in[7:1], 1'b0};

endmodule
