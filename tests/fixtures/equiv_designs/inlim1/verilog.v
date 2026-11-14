// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16: an index `i - 1` with i from 0: v[-1] is outside the range in the first pass, so the
// select is not proven in range and the constant call shl(8'h81) is refused.
`default_nettype none
module inlim1 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] shl(input [7:0] v);
    integer i;
    begin
      shl = 8'd0;
      for (i = 0; i < 8; i = i + 1)
        if (v[i - 1]) shl[i] = 1'b1;
    end
  endfunction
  assign uo_out = ui_in ^ shl(8'h81);
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
