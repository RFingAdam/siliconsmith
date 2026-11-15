// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: the same call as in realparam, in a parameter override: the child's P is 1 for
// both Verilator and a simulator, 2 for Yosys.
`default_nettype none
module realover_sub #(parameter [7:0] P = 8'd0) (input wire [7:0] a, output wire [7:0] y);
  assign y = a ^ P;
endmodule

module realover (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 1.0e999;
  function automatic [7:0] g(input integer u);
    case (u)
      u: g = 8'd1;
      default: g = 8'd2;
    endcase
  endfunction
  /* verilator lint_off REALCVT */
  realover_sub #(.P(g(R))) u_sub (.a(ui_in), .y(uo_out));
  /* verilator lint_on REALCVT */
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
