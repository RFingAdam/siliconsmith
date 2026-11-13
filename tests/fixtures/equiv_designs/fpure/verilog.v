// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: Apache-2.0

// fpure: pure functions only. Each function uses just its arguments, its own locals and
// constants, so a simulator's `always @*` sees everything that changes the result.
`default_nettype none
module fpure (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam [7:0] K = 8'd3;
  reg [7:0] cnt;
  reg [7:0] y;
  always @(posedge clk)
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[0]) cnt <= cnt + 8'd1;
  function automatic [7:0] rot(input [7:0] a);
    rot = {a[6:0], a[7]} ^ K;
  endfunction
  function automatic [7:0] mix(input [7:0] a, input [7:0] b);
    reg [7:0] t;
    integer i;
    begin
      t = a;
      for (i = 0; i < 2; i = i + 1) t = rot(t);
      mix = t ^ b ^ $bits(cnt);
    end
  endfunction
  always @(*) y = mix(cnt, ui_in);
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in, 1'b0};
endmodule
