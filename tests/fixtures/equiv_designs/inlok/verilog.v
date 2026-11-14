// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 15, the control: the function with a computed index called with a signal and, with
// constant arguments, in a localparam (in range: every tool gives 1); functions without such a
// select (a mask, a rotation) called with constant arguments inline. It must stay equivalent.
`default_nettype none
module inlok (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  // a function that reads a vector at a computed index: fine with a signal argument, and
  // with constant arguments in a localparam (the select stays in range: every tool gives 1)
/* verilator lint_off UNUSEDSIGNAL */
  function automatic integer pick(input [3:0] v, input integer k);
    if (!v[k]) pick = 0; else pick = 1;
  endfunction
/* verilator lint_on UNUSEDSIGNAL */
  function automatic [7:0] rev(input [7:0] v);
    for (int i = 0; i < 8; i++) rev[i] = v[7 - i];
  endfunction
  // functions without such a select: fine with constant arguments anywhere
  function automatic [7:0] low_mask(input [2:0] n);
    integer k;
    begin
      low_mask = 8'd0;
      for (k = 0; k < 8; k = k + 1)
        if (k <= n) low_mask[k] = 1'b1;
    end
  endfunction
  function automatic [7:0] rot(input [7:0] v, input [2:0] n);
    rot = (v << n) | (v >> (4'd8 - {1'b0, n}));
  endfunction
  localparam integer M = pick(4'b1010, 1);
  reg [7:0] y;
  always @(*) begin
    y = rev(ui_in) ^ low_mask(3'd5) ^ rot(8'h81, 3'd2);
    if (M == 1) y = ~y;
    if (pick(ui_in[3:0], 2) != 0) y = y ^ 8'h0f;
  end
  assign uo_out = y;
  assign uio_out = 8'd0; assign uio_oe = 8'd0;
  wire _unused = &{1'b0, uio_in, ui_in, ena, clk, rst_n};
endmodule
