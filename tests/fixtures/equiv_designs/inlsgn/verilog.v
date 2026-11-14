// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16: a signed index (`input signed [3:0] k`, -8 to 7) into an 8-bit vector: a negative k
// is outside the range, so the select is not proven in range and the constant call
// pick(8'hff, 4'sb1111) (k = -1) is refused.
`default_nettype none
module inlsgn (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off WIDTHTRUNC */
  function automatic integer pick(input [7:0] v, input signed [3:0] k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on WIDTHTRUNC */
/* verilator lint_on UNUSEDSIGNAL */
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (pick(8'hff, 4'sb1111) == 0) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
