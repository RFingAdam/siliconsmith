// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 17 (review design arrelc): `v[t[2]]` with `reg [1:0] t [0:3]` and t[2] = 3, outside the
// 3-bit v, in a function called with constants as a case label. Icarus gives uo_out 1 for
// ui_in[0] = 1 and 2 for 0; the gates give 0. Refused.
`default_nettype none
module arrelc (
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
  reg [7:0] y;
  always @(*) begin
    y = 8'd0;
    case ({31'd0, ui_in[0]})
      pick(2'd0): y = 8'd1;
      default: y = 8'd2;
    endcase
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
