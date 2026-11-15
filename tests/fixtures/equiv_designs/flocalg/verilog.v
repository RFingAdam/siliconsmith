// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 21 (r15): lane masks worked out by a function in each generate pass; the function keeps
// its width in a local localparam and passes it to a helper with its argument. Equivalent.
`default_nettype none
module flocalg (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  function automatic [7:0] ones(input integer n, input integer w);
    integer k;
    begin
      ones = 8'd0;
      for (k = 0; k < w; k = k + 1) if (k < n) ones[k] = 1'b1;
    end
  endfunction
  function automatic [7:0] lane_mask(input integer lane);
    localparam W = 8;
    lane_mask = ones(lane + 1, W);
  endfunction
  genvar i;
  generate
    for (i = 0; i < 4; i = i + 1) begin : lane
      localparam [7:0] M = lane_mask(i);
      assign uo_out[2*i +: 2] = {^(ui_in & M), |(uio_in & M)};
    end
  endgenerate
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, ena, clk, rst_n};
endmodule
