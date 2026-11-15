// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21 (r17): a small helper library with one function not used yet; it passes its local
// localparam to another helper with its argument. Verilator never builds the unused function,
// the simulator lists HALF as 4. Equivalent.
`default_nettype none
module flocalu (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] rotl(input [7:0] v, input integer n);
    rotl = (v << n) | (v >> (8 - n));
  endfunction
  function automatic [7:0] swap_nibbles(input [7:0] v);
    localparam HALF = 4;
    swap_nibbles = rotl(v, HALF);
  endfunction
  function automatic [7:0] inc(input [7:0] v);
    inc = v + 8'd1;
  endfunction
  assign uo_out = inc(ui_in);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
