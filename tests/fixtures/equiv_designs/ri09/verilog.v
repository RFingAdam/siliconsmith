// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18: a real localparam that is infinite (1.0e999), cast to an integer and passed to a
// function whose select is proven inside its range (`v[a[3:0]]`, 4 bits into 16). Icarus converts
// an infinite real to an all-x integer, so `v[a]` reads x and the `if` takes the else branch
// (f = 2); Verilator gives 1, Yosys reads x as a value of its own choice, and the gates differ
// from the simulator (RTL 0d/58/83 for ui_in 0f/5a/81, gates 08/58/80). A real argument can be
// x: refused.
`default_nettype none
module ri09 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 1.0e999;
  function automatic [7:0] f(input [31:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a[3:0]]) f = 8'h01; else f = 8'h02;
      if (a[31:4] != 28'd0) f = 8'h04;
    end
  endfunction
  assign uo_out = ui_in ^ f(integer'(R));
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
