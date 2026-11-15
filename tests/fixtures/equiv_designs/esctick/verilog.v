// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 20: a back-tick inside an escaped identifier. Icarus expands the predefined macro
// __ICARUS__ inside the name and reads `\v1`, which inside block g is g's own `\v1 = ~ui_in`;
// Yosys and Verilator read the module's wire.
`default_nettype none
module esctick (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [7:0] \v`__ICARUS__ = ui_in;
  if (1) begin : g
    wire [7:0] \v1 = ~ui_in;
    assign uo_out = \v`__ICARUS__ ^ (\v1 & 8'h00);
  end
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
