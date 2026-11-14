// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

`default_nettype none
/* verilator lint_off UNUSEDSIGNAL */
module eqx (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  reg r;
  always @(posedge clk) if (ui_in[0]) r <= 1'b1;
  assign uo_out = {7'd0, r === 1'bx};
endmodule
