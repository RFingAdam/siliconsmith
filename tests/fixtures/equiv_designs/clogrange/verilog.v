// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 27 (w05): a table of the input rotated left by i + 1 for i = 0 to 7, built in a
// generate loop with a rotate helper whose shift amount is `input [$clog2(N)-1:0] n` (3 bits;
// i + 1 wraps at N, as a rotate should). The argument is 32 bits, wider than the input, so
// nothing can carry; the check refused it at 6da0a89 because it could not work out the width
// `$clog2(N) - 1`. Equivalent.
`default_nettype none
module clogrange (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  localparam N = 8;
  function automatic [N-1:0] rotl(input [N-1:0] x, input [$clog2(N)-1:0] n);
    rotl = (x << n) | (x >> (N - n));
  endfunction
  wire [N-1:0] tbl [0:N-1];
  genvar i;
  generate
    for (i = 0; i < N; i = i + 1) begin : g
      /* verilator lint_off WIDTHTRUNC */
      assign tbl[i] = rotl(ui_in, i + 1);
      /* verilator lint_on WIDTHTRUNC */
    end
  endgenerate
  assign uo_out  = tbl[uio_in[2:0]];
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{1'b0, uio_in[7:3], ena, clk, rst_n};
endmodule
