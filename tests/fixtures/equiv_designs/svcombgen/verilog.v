// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// probe: always_comb (no always @*, no function) that reads t before it writes it, in the
// generate branch that Yosys keeps (1/0 is x to Yosys) and Verilator drops.
`default_nettype none
module svcombgen (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Z = 0;
  localparam integer Q = 1 / Z;
  reg [7:0] y, t;
  if (Q == 0) begin : g
    always_comb begin
      t = ui_in;
      y = t;
    end
  end else begin : g
    always_comb begin
      y = t;
      t = ui_in;
    end
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
