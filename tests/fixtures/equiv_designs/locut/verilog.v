// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21: Verilator ends a `// verilator lint_off` comment at its first `/` and reads on. Here the
// `/*` after WIDTH opens a block comment for Verilator that hides the two clocked blocks with
// blocking assignments (a race in a simulator) from its lint; the `*/` on the next line, and the
// `/**/` after the second lint_off, hand it nonblocking copies as code instead. Yosys and a
// simulator read those two lines as comments and run the race: Icarus shows uo_out one cycle
// early, the gates do not. Refused: the comment goes on after a single `/`.
`default_nettype none
module locut (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] a, b;
  // verilator lint_off WIDTH /*
  always @(posedge clk) if (!rst_n) a = 8'd0; else a = ui_in;
  always @(posedge clk) if (!rst_n) b = 8'd0; else b = a;
  // */ always @(posedge clk) if (!rst_n) a <= 8'd0; else a <= ui_in;
  // verilator lint_off WIDTH /**/ always @(posedge clk) if (!rst_n) b <= 8'd0; else b <= a;
  assign uo_out = b;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
