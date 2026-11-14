// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A void function with an inout argument: `bump(t)` reads `t` before the block writes it on the
// ui_in[0] = 0 path (round 13).
module fninout(input wire [7:0] ui_in, output wire [7:0] uo_out, input wire [7:0] uio_in, output wire [7:0] uio_out, output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n);
  assign uio_out = 8'h00;
  assign uio_oe = 8'h00;
  wire _unused = &{ena, clk, rst_n, ui_in[7:1], 1'b0};
  reg [7:0] t, y;
  function automatic void bump(inout [7:0] v);
    v = v + 8'd1;
  endfunction
  always @(*) begin
    if (ui_in[0]) t = uio_in;
    bump(t);
    y = t;
    t = uio_in ^ 8'h55;
  end
  assign uo_out = y;
endmodule
