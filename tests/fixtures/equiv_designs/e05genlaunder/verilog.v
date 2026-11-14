// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// The same laundered localparam (1 in Icarus, 0 in Verilator and Yosys) picks which bit of
// uo_out shows ui_in[0], so a simulator and the netlist drive different pins (round 15).
`default_nettype none
module e05genlaunder (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  localparam integer M = pick(4'b1010, 6);
  reg [7:0] y;
  always @(*) begin
    if (M == 1) y = {ui_in[0], 7'd0};
    else y = {7'd0, ui_in[0]};
  end
  assign uo_out = y; assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in[7:1], ena, clk, rst_n};
endmodule
