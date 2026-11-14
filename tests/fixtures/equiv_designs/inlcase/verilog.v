// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15 (inline form of c02ifnot): a call of a function that reads v[6] of a 4-bit argument,
// with constant arguments, as a case label. Icarus reads x there, `if (!x)` takes the else
// branch and the label is 1; Yosys and Verilator use 0. Refused: compute it in a localparam.
`default_nettype none
module inlcase (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  reg [7:0] y;
  always @(*) begin
    y = 8'd0;
    case ({31'd0, ui_in[0]})
      pick(4'b1010, 6): y = 8'd1;
      default: y = 8'd2;
    endcase
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
