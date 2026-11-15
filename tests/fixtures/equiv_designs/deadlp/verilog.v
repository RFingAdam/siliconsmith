// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19 control: a localparam in a generate branch that the parameters do not choose, passed
// to a helper with a proven select. No tool builds that branch, so the call is dead code in every
// tool and does not need a plain value. Equivalent.
`default_nettype none
module deadlp (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  parameter USE_REV = 0;
  function automatic [7:0] rev8;
    input [7:0] v;
    integer i;
    begin
      rev8 = 8'd0;
      for (i = 0; i < 8; i = i + 1) rev8[i] = v[7 - i];
    end
  endfunction
  generate
    if (USE_REV) begin : g_rev
      localparam [7:0] M = 8'h12;
      assign uo_out = ui_in ^ rev8(M);
    end else begin : g_plain
      assign uo_out = ui_in;
    end
  endgenerate
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
