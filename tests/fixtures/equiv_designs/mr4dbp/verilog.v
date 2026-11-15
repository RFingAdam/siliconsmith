// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18 control: the call cycle of mr4db with an 8-bit v, so f's select v[k] (k 3 bits) is
// proven inside the range, and so is g's call of f. Equivalent.
`default_nettype none
module mr4dbp (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] f(input [7:0] v, input [2:0] k, input d);
    begin
      if (d) $display("%h", g(v, k));
      if (!v[k]) f = 8'd1;
      else f = 8'd2;
    end
  endfunction
  function automatic [7:0] g(input [7:0] v, input [2:0] k);
    g = f(v, k, 1'b0);
  endfunction
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (g(8'hBF, 3'd6) == 8'd2) y = ~ui_in;
  end
  localparam [7:0] P0 = f(8'hBF, 3'd1, 1'b0);
  assign uo_out = y ^ P0;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
