// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A function called by a hierarchical name split over two lines (`g\n.rd(ui_in)`).
`default_nettype none
module hiersplit (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  if (1) begin : g
    function automatic [7:0] rd(input [7:0] d);
      rd = d ^ 8'h5a;
    endfunction
  end
  wire [7:0] y;
  assign y = g
    .rd(ui_in);
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
