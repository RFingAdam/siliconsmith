// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

`default_nettype none
/* verilator lint_off CASEINCOMPLETE */
module fccomblo (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  reg [1:0] y;
  always @* begin
    y = 2'b11;
    (* full_case *)
    case (ui_in[1:0])
      2'b00: y = 2'b00;
      2'b01: y = 2'b01;
      2'b10: y = 2'b10;
    endcase
  end
  assign uo_out = {6'd0, y};
  wire _unused = &{ui_in[7:2], uio_in, ena, clk, rst_n, 1'b0};
endmodule
