// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 17 (review design arrelf): the function of arrelc called with constants inside another
// function. Icarus gives uo_out = ui_in; the gates give ~ui_in. Refused.
`default_nettype none
module arrelf (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic integer pick(input [1:0] k);
    reg [1:0] t [0:3];
    reg [2:0] v;
    begin
      t[0] = 2'd0; t[1] = 2'd1; t[2] = 2'd3; t[3] = k;
      v = 3'b010;
      if (!v[t[2]]) pick = 0; else pick = 1;
    end
  endfunction
  function automatic [7:0] h(input [7:0] a);
    h = pick(2'd0) != 0 ? a : ~a;
  endfunction
  assign uo_out = h(ui_in);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
