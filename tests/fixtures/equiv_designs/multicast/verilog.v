// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 26 (p06clog2sub): two wrap-around counters of lengths 4 and 10 from one module. The
// last index is cast to the counter's width, AW'(DEPTH - 1): a 32-bit value cut to AW bits, the
// same in every tool. AW has two values (2 and 4), so the check could not work out the cast's
// width and refused it at 97f3bdb. It is now judged with each copy's own values. Equivalent.
`default_nettype none
module multicast_wrap #(parameter DEPTH = 4) (input wire clk, input wire rst_n, input wire en,
    output wire [3:0] idx, output wire full, output wire [2:0] widths);
  function automatic integer clog2(input integer value);
    integer i;
    begin
      clog2 = 0;
      for (i = 0; i < 31; i = i + 1)
        if ((1 << i) < value) clog2 = i + 1;
    end
  endfunction
  localparam AW = clog2(DEPTH);
  localparam CW = clog2(DEPTH + 1);
  localparam LW = clog2(DEPTH - 1);
  localparam [AW-1:0] LAST = AW'(DEPTH - 1);
  reg [AW-1:0] q;
  always @(posedge clk) begin
    if (!rst_n) q <= {AW{1'b0}};
    else if (en) q <= (q == LAST) ? {AW{1'b0}} : q + 1'b1;
  end
  assign idx = {{(4-AW){1'b0}}, q};
  assign full = (q == LAST);
  assign widths = {AW == 4, CW == 4, LW == 4};
endmodule

module multicast (
    input  wire [7:0] ui_in, output wire [7:0] uo_out, input  wire [7:0] uio_in,
    output wire [7:0] uio_out, output wire [7:0] uio_oe, input  wire ena,
    input  wire clk, input  wire rst_n);
  wire [3:0] a, b;
  wire fa, fb;
  wire [2:0] wa, wb;
  multicast_wrap #(.DEPTH(4)) u4 (.clk(clk), .rst_n(rst_n), .en(ui_in[0]), .idx(a), .full(fa),
                                  .widths(wa));
  multicast_wrap #(.DEPTH(10)) u10 (.clk(clk), .rst_n(rst_n), .en(ui_in[1]), .idx(b),
                                    .full(fb), .widths(wb));
  assign uo_out = {b, a};
  assign uio_out = {fa, fb, wa, wb};
  assign uio_oe = 8'hFF;
  wire _unused = &{1'b0, ui_in[7:2], uio_in, ena};
endmodule
