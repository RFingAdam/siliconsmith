// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// An attribute ends for Verilator at a *) inside its string, not for Yosys.
`default_nettype none
module attrhide (
    input  wire [7:0] ui_in, output reg [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  reg s;
  always @* begin
    s = 1'b0;
    uo_out = ui_in;
    (* note = "*) uo_out = ~ui_in; //" *) s = 1'b0;
  end
  wire _unused = &{uio_in, ena, clk, rst_n, s, 1'b0};
endmodule
