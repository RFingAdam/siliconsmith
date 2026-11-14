// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// probe: an always @(*) that reads t before it writes it in ONE statement (a concatenation
// target), in the generate branch Yosys keeps (1/0) and Verilator drops. Without the generate
// the same block is refused (concat.v): Verilator's always_comb lint finds it.
`default_nettype none
module concatgen (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  reg [7:0] y, t;
  if (Q == 0) begin : g
    always @(*) {t, y} = {ui_in, ui_in};
  end else begin : g
    always @(*) {y, t} = {t, ui_in};
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, t, 1'b0};
endmodule
