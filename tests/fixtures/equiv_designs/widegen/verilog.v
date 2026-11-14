// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 p24genbr64: an untyped localparam that is 2'b11 to Icarus and 1'h1 to Verilator (the
// bit both hold agrees): Icarus takes the generate branch ga, the gates gb.
`default_nettype none
module widegen (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  localparam P = {pick(4'b1010, 64) + 1 {1'b1}};
/* verilator lint_off WIDTHEXPAND */
  if (P == 3) begin : ga
/* verilator lint_on WIDTHEXPAND */
    assign uo_out = ~ui_in;
  end else begin : gb
    assign uo_out = ui_in;
  end
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
