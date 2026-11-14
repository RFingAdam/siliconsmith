// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16: a loop that runs one pass too far (`i <= 8` over an 8-bit vector): v[8] is outside
// the range, so the select is not proven in range and the constant call ones(8'hff) is refused.
`default_nettype none
module inlle8 (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [3:0] ones(input [7:0] v);
    integer i;
    begin
      ones = 4'd0;
      for (i = 0; i <= 8; i = i + 1)
        if (v[i]) ones = ones + 4'd1;
    end
  endfunction
  reg [7:0] y;
  always @(*) begin
    y = ui_in;
    if (ones(8'hff) == 4'd8) y = ~ui_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
