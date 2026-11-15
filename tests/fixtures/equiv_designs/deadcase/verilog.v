// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19 control: a generate case whose branches each declare a localparam `M` and call the
// same helper with it. Only the default branch is built, the others are dead code in every tool.
// Equivalent.
`default_nettype none
module deadcase (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  parameter MODE = 2;
  function automatic [7:0] rev8;
    input [7:0] v;
    integer i;
    begin
      rev8 = 8'd0;
      for (i = 0; i < 8; i = i + 1) rev8[i] = v[7 - i];
    end
  endfunction
  generate
    case (MODE)
      0: begin : m0
        localparam [7:0] M = 8'h01;
        assign uo_out = ui_in ^ rev8(M);
      end
      1: begin : m1
        localparam [7:0] M = 8'h03;
        assign uo_out = ui_in ^ rev8(M);
      end
      default: begin : md
        localparam [7:0] M = 8'h07;
        assign uo_out = ui_in ^ rev8(M);
      end
    endcase
  endgenerate
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
