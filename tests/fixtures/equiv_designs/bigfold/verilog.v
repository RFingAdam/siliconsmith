// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// `if (2147483648 > 0)`: Verilator reads the unsized number as a negative 32-bit value and keeps
// only the else branch; Yosys and Icarus take the then branch, which reads `t` before it writes
// it on one path (round 13).
`default_nettype none
module bigfold (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] y, t;
  always @(*) begin
    if (2147483648 > 0) begin
      if (ui_in[0]) begin
        t = uio_in;
        y = t;
      end else begin
        y = t;
        t = uio_in;
      end
    end else begin
      t = uio_in;
      y = t;
    end
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in[7:1], 1'b0};
endmodule
