// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 18: f(v, k, d) reads v[k] (a 3-bit k into a 6-bit v: 6 and 7 are outside) and calls
// g(v, k) inside an `if (d) $display(...)` that never runs; g(v, k) = f(v, k, 1'b0). The
// localparam P0 that calls f comes after the always block, so the check met it first and used to
// cache g as safe while f was still being judged: `g(6'h3F, 3'd6)` in the always block passed.
// Icarus reads x at v[6], `!x` takes the else branch (y = ~ui_in); the gates do not. Refused.
`default_nettype none
module mr4db (
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
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (g(6'h3F, 3'd6) == 8'd2) y = ~ui_in;
  end
  localparam [7:0] P0 = f(6'h3F, 3'd1, 1'b0);
  assign uo_out = y ^ P0;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
