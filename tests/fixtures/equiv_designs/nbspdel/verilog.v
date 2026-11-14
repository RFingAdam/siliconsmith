// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// The same trick hides a delay (`#7;`) from a lexer that ends an escaped identifier
// at a no-break space.
module nbspdel(input wire [7:0] ui_in, output reg [7:0] uo_out, input wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n);
  assign uio_out = 8'h00;
  assign uio_oe = 8'h00;
  reg \h /* , \e*/ ;
  wire _unused = &{rst_n, ena, uio_in, \h /* , \e*/ , 1'b0};
  always @(posedge clk) begin
    \h /* <= 1'b0; #7; \e*/ <= 1'b0;
    uo_out <= ui_in;
  end
endmodule
