// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A localparam from a constant function that reads `v[6]` of a 4-bit `v`: x to Icarus, a defined value to Verilator (round 14).
`default_nettype none
module qf2(input wire [7:0] ui_in, output wire [7:0] uo_out, input wire [7:0] uio_in, output wire [7:0] uio_out, output wire [7:0] uio_oe, input wire ena, input wire clk, input wire rst_n);
  assign uio_out = 8'h00;
  assign uio_oe = 8'h00;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
  function automatic [0:0] pick(input integer i);
    reg [3:0] v;
    integer k;
    begin
      v = 4'b1010;
      pick = 1'b0;
      for (k = 0; k < 8; k = k + 1) if (k == i) pick = v[k];
    end
  endfunction
  localparam [0:0] Q = pick(6);
  assign uo_out = ui_in ^ {7'd0, Q};
endmodule
