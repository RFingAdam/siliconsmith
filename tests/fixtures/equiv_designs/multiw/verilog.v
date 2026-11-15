// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 26 (p29multiw): a saturating up-counter module used twice, 4 and 6 bits wide. The
// count is clamped with a helper clamp(c + 1'b1, TOP - 1'b1) whose inputs are as wide as the
// counter. TOP - 1'b1 is exactly as wide as the input in each copy, but the input's width and
// TOP depend on W, which has two values, so the check could work out neither and refused it at
// 97f3bdb. It is now judged with each copy's own values. Equivalent.
`default_nettype none
module multiw_sat #(parameter W = 4) (input wire clk, input wire rst_n, input wire en,
    output wire [W-1:0] q);
  localparam [W-1:0] TOP = {W{1'b1}};
  function automatic [W-1:0] clamp(input [W-1:0] v, input [W-1:0] lim);
    clamp = (v > lim) ? lim : v;
  endfunction
  reg [W-1:0] c;
  always @(posedge clk) begin
    if (!rst_n) c <= {W{1'b0}};
    else if (en) c <= clamp(c + 1'b1, TOP - 1'b1);
  end
  assign q = c;
endmodule

module multiw (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [3:0] a;
  wire [5:0] b;
  multiw_sat #(.W(4)) u4 (.clk(clk), .rst_n(rst_n), .en(ui_in[0]), .q(a));
  multiw_sat #(.W(6)) u6 (.clk(clk), .rst_n(rst_n), .en(ui_in[1]), .q(b));
  assign uo_out = {b[3:0], a};
  assign uio_out = {6'd0, b[5:4]};
  assign uio_oe = 8'h03;
  wire _unused = &{1'b0, ui_in[7:2], uio_in, ena};
endmodule
