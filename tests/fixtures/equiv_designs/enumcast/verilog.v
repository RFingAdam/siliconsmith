// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 27 (w49): a typedef enum mode picked from input bits with mode_t'(ui_in[1:0]); a
// decoder helper with a plain 2-bit input is called with the cast and with the registered
// mode. An enum has the width of its base type (2 bits here) and the argument reads a signal,
// so nothing can carry; the check refused it at 6da0a89 as a width it could not work out (a
// `REFDTYPE` node). Equivalent.
`default_nettype none
module enumcast (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  typedef enum logic [1:0] {OFF = 2'd0, SLOW = 2'd1, FAST = 2'd2, TEST = 2'd3} mode_t;
  function automatic [3:0] dec(input [1:0] m);
    dec = 4'b0001 << m;
  endfunction
  mode_t md;
  always @(posedge clk) begin
    if (!rst_n) md <= OFF;
    else if (ui_in[2]) md <= mode_t'(ui_in[1:0]);
  end
  assign uo_out  = {dec(mode_t'(ui_in[1:0])), dec(md)};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{1'b0, ui_in[7:3], uio_in, ena};
endmodule
