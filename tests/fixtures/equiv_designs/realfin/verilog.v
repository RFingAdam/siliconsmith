// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 19 control of realcase: the same case label from a finite real (15.0), which both tools
// convert to the number 15. Equivalent.
`default_nettype none
module realfin (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam real R = 15.0;
  wire [3:0] w = 4'(integer'(R));
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    case (ui_in[3:0])
      w: y = ~ui_in;
      default: y = ui_in;
    endcase
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ena, clk, rst_n};
endmodule
