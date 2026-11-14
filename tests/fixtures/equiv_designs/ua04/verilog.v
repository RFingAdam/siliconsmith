// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 17 (review design ua04): `v[m[0]]` reads a 3-bit argument at an element of a local array
// `reg [1:0] m [0:1]`, set to 3. The element is 2 bits (0 to 3), so the index can be outside v;
// round 16 took it as one bit (0 or 1) and proved it inside. Icarus reads x at v[3], the `if`
// takes the else branch and pick is 0 (uo_out = ~ui_in); the gates give pick 1. Refused.
`default_nettype none
module ua04 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic integer pick(input [2:0] v);
    reg [1:0] m [0:1];
    begin
      m[0] = 2'd3;
      m[1] = 2'd0;
      if (v[m[0]]) pick = 1; else pick = 0;
    end
  endfunction
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (pick(3'b111) == 0) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
