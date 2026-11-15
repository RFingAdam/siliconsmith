// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19: a clocked block that stores an infinite real in a register. A simulator stores all
// x and `!w[3]` takes the else branch; Yosys converts the real with a warning and builds gates that
// need not agree. Refused.
`default_nettype none
module realff (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 1.0e999;
  reg [15:0] w;
  reg [7:0] y;
  always @(posedge clk) begin
    /* verilator lint_off REALCVT */
    w <= R;
    /* verilator lint_on REALCVT */
  end
  always @(*) begin
    y = ui_in;
    if (!w[3]) y = ~ui_in;
  end
  assign uo_out = y;
  wire _unused2 = &{1'b0, w};
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
