// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// SystemVerilog `unique case` is read by synthesis as full and parallel: the gates may differ
// from what a simulator runs.
`default_nettype none
module uniquecase (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg y;
  always @* begin
    y = 1'b0;
    unique case (ui_in[1:0])
      2'd0: y = 1'b1;
      2'd1: y = 1'b0;
      2'd2: y = ui_in[2];
      2'd3: y = ui_in[3];
    endcase
  end
  assign uo_out = {7'd0, y};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{uio_in, ena, clk, rst_n, ui_in[7:4], 1'b0};
endmodule
