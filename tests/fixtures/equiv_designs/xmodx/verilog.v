// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// `5 % 0` steering a generate `case` of plain assignments (round 13).
`default_nettype none
module xmodx (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam integer Q = 5 % 0;
  case (Q)
    Q + 1: begin : g
      assign uo_out = ~ui_in;
    end
    default: begin : g
      assign uo_out = ui_in;
    end
  endcase
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
