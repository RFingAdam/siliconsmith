// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// `(2147483648 > 0) ? rd(uio_in) : uio_in`: Verilator folds the call away, so the purity check
// would not see that `rd` reads the counter `cnt` (round 13).
`default_nettype none
module bigimpure (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  reg [7:0] cnt;
  always @(posedge clk) begin
    if (!rst_n) cnt <= 8'd0;
    else if (ui_in[1]) cnt <= cnt + 8'd1;
  end
  function automatic [7:0] rd(input [7:0] d);
    rd = d ^ cnt;
  endfunction
  reg [7:0] y;
  always @(*) begin
    y = (2147483648 > 0) ? rd(uio_in) : uio_in;
  end
  assign uo_out = y;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, ui_in[7:2], ui_in[0], 1'b0};
endmodule
