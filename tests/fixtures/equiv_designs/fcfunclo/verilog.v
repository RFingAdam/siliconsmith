// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

`default_nettype none
/* verilator lint_off CASEINCOMPLETE */
module fcfunclo (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  function [1:0] f(input [1:0] s);
    begin
      f = 2'b11;
      (* full_case *)
      case (s)
        2'b00: f = 2'b00;
        2'b01: f = 2'b01;
        2'b10: f = 2'b10;
      endcase
    end
  endfunction
  assign uo_out = {6'd0, f(ui_in[1:0])};
  wire _unused = &{ui_in[7:2], uio_in, ena, clk, rst_n, 1'b0};
endmodule
