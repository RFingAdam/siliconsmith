// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: an infinite real as the argument of a call in a localparam value. Verilator and a
// simulator both give K the value 1 (a simulator's case matches its x with x), Yosys works the
// call out itself, makes the real all x without a warning and takes the default: K = 2. The two
// tools that compare parameter values agree, so only refusing the real argument catches it.
`default_nettype none
module realparam (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 1.0e999;
  function automatic [7:0] g(input [7:0] u);
    case (u)
      u: g = 8'd1;
      default: g = 8'd2;
    endcase
  endfunction
  /* verilator lint_off REALCVT */
  localparam [7:0] K = g(R);
  /* verilator lint_on REALCVT */
  assign uo_out = ui_in ^ K;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
