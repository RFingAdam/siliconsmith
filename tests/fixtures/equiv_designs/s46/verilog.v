// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (round 15 note s46): a parity helper called with a genvar in a generate loop. Its loop
// `for (k = 0; k < 3; k = k + 1)` gives k the values 0 to 2 in the body, so v[k] stays inside the
// 3-bit argument: proven in range, and the constant call is fine. Must be equivalent.
`default_nettype none
module s46 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  function automatic parity3(input [2:0] v);
    integer k;
    begin
      parity3 = 1'b0;
      for (k = 0; k < 3; k = k + 1) parity3 = parity3 ^ v[k];
    end
  endfunction
  genvar g;
  generate
    for (g = 0; g < 8; g = g + 1) begin : b
      assign uo_out[g] = ui_in[g] ^ parity3(g);
    end
  endgenerate
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, uio_in, 1'b0};
endmodule
