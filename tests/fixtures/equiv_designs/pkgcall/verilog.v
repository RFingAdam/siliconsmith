// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

package p66_pkg;
  function automatic [7:0] mix(input [7:0] a, input [7:0] b);
    mix = a ^ {b[3:0], b[7:4]};
  endfunction
endpackage
`default_nettype none
module pkgcall (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else cnt <= cnt + 8'd1;
  assign uo_out = p66_pkg::mix(cnt, ui_in);
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, ui_in, clk, rst_n, 1'b0};
endmodule
