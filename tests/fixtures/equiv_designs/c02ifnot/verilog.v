// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A localparam from a constant function that reads a bit outside its 4-bit argument (`v[6]`):
// Icarus reads x, `if (!x)` takes the else branch and M is 1; Verilator and Yosys fold it to 0.
// The always block then reads tt[1] before it writes it in a simulator only (round 15).
`default_nettype none
module c02ifnot (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  localparam integer M = pick(4'b1010, 6);
  reg tt [0:1];
  reg [7:0] y;
  always @(*) begin
    tt[0] = ui_in[0];
    y = {7'd0, tt[M]};
    tt[1] = ui_in[1];
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in[7:2], ena, clk, rst_n};
endmodule
