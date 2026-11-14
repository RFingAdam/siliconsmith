// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 17 (review design locsel2): `v[loc[1:0]]` with a local loc that only k == 3 sets, so for
// pick(2'd0) the index is x. A part-select of a local is not a proven operand (round 16 took it
// as 0 to 3). Icarus gives pick 1 (uo_out = ~ui_in); the gates give pick 0. Refused.
`default_nettype none
module locsel2 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic integer pick(input [1:0] k);
    reg [1:0] loc;
    reg [3:0] v;
    begin
      v = 4'b1010;
      if (k == 2'd3) loc = 2'd1;
      if (!v[loc[1:0]]) pick = 0; else pick = 1;
    end
  endfunction
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (pick(2'd0) == 1) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
