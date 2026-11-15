// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18 control: the function of ri09 called with plain integer parameters (a typed and an
// untyped localparam, and a child's parameter set by an override): each has one number in both
// tools, so the proven select stays proven. Equivalent.
`default_nettype none
module realok (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer R = 3;
  localparam S = 5;
  function automatic [7:0] f(input [31:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a[3:0]]) f = 8'h01; else f = 8'h02;
      if (a[31:4] != 28'd0) f = 8'h04;
    end
  endfunction
  wire [7:0] o;
  realok_sub #(.P(7)) u (.o(o));
  assign uo_out = ui_in ^ f(R) ^ f(S + 1) ^ o;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule

module realok_sub #(parameter P = 0) (output wire [7:0] o);
  function automatic [7:0] f(input [31:0] a);
    reg [15:0] v;
    begin
      v = 16'hA5A5;
      if (v[a[3:0]]) f = 8'h01; else f = 8'h02;
      if (a[31:4] != 28'd0) f = 8'h04;
    end
  endfunction
  assign o = f(P);
endmodule
