// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// In Verilog tools only space, tab, CR and LF end an escaped identifier, so after the
// no-break space the `/* ... */` below is code for them. A lexer that ends the identifier
// at the no-break space reads it as a comment and hides a translate_off region.
module nbspoff2(input wire [7:0] ui_in, output reg [7:0] uo_out, input wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n);
  assign uio_out = 8'h00;
  assign uio_oe = 8'h00;
  reg \h /* , \e*/ ;
  wire _unused = &{clk, rst_n, ena, uio_in, \h /* , \e*/ , 1'b0};
  always @* begin
    \h /* = 1'b0; \e*/ = 1'b0;
    uo_out = ui_in;
    // synopsys translate_off
    \h /* = 1'b0;
    uo_out = ~ui_in;
    \e*/ = 1'b0;
    // synopsys translate_on
  end
endmodule
