// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 24 (fpart3): an untyped localparam inside a function that overflows only in the module
// copy where Verilator works the function out as a constant (u2, P = 2: a wire's range). In u1
// the function is called at run time, so Verilator lists M there, with the value Icarus gives
// (2147483648); it leaves u2's copy out. Icarus lists M in u1 and u2 with that value, so the
// values the two tools list agreed. In u2 Verilator and Yosys wrap M to 0, so w is 4 bits in the
// gates and 8 in Icarus: Icarus gives uo_out = ui_in ^ ff, the gates ui_in ^ 0f, and the check
// said equivalent at d820087. Refused: Verilator does not list the local in every copy.
`default_nettype none
module fsub #(parameter [31:0] P = 1) (input wire [7:0] a, output wire [7:0] y);
  function automatic [7:0] f(input [7:0] x);
    localparam M = ((32'h8000_0000 * P) >> 1) * (32'd3 - P);
    begin
      f = (M != 0) ? x : 8'd3;
    end
  endfunction
  if (P == 1) begin : g
    assign y = f(a);
  end else begin : h
    wire [f(8'd7):0] w;
    assign w = '1;
    wire _unused_a = &{1'b0, a};
    /* verilator lint_off WIDTHEXPAND */
    assign y = w;
    /* verilator lint_on WIDTHEXPAND */
  end
endmodule

module fpart3 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [7:0] y1, y2;
  fsub #(.P(1)) u1 (.a(ui_in), .y(y1));
  fsub #(.P(2)) u2 (.a(ui_in), .y(y2));
  assign uo_out = y1 ^ y2;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
