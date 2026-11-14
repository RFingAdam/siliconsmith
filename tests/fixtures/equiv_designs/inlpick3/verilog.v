// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16: the inline-call seed with a 3-bit index into a 4-bit vector (`pick(v, k)`, k from 0
// to 7): k can be outside v's range, so the select is not proven in range. Icarus reads x at
// v[6], `if (!x)` takes the else branch and pick is 1; Yosys and Verilator use 0. Refused.
`default_nettype none
module inlpick3 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
/* verilator lint_off WIDTHTRUNC */
  function automatic integer pick(input [3:0] v, input [2:0] k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on WIDTHTRUNC */
/* verilator lint_on UNUSEDSIGNAL */
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (pick(4'b1010, 3'd6) == 1) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
