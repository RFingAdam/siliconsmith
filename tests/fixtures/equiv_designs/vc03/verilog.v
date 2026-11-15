// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18: the call cycle of mr4db in the value of a real localparam (RR = g(6'h3F, 3'd6)),
// whose value is not compared with the simulator's: it must be named as such and refused.
`default_nettype none
module vc03 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [5:0] v, input [2:0] k, input d);
    begin
      if (d) $display("%h", g(v, k));
      if (!v[k]) f = 8'd1;
      else f = 8'd2;
    end
  endfunction
  function automatic [7:0] g(input [5:0] v, input [2:0] k);
    g = f(v, k, 1'b0);
  endfunction
  localparam real RR = g(6'h3F, 3'd6);
  assign uo_out = ((RR > 1.5) ? ui_in : ~ui_in) ^ P0;
  localparam [7:0] P0 = f(6'h3F, 3'd1, 1'b0);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
