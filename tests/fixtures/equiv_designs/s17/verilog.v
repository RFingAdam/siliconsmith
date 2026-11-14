// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (round 15 note s17): a seven-segment helper function that reads a 112-bit table at
// SEGS[d*7 +: 7], called with the constant 4'hE in an always block. d is a 4-bit argument, so
// d*7 is 0 to 105 and the part-select stays inside SEGS for every value: it is proven in range
// and the constant call is fine. Must be equivalent.
`default_nettype none
module s17 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  localparam [111:0] SEGS = {7'h71, 7'h79, 7'h5e, 7'h39, 7'h7c, 7'h77, 7'h6f, 7'h7f,
                             7'h07, 7'h7d, 7'h6d, 7'h66, 7'h4f, 7'h5b, 7'h06, 7'h3f};
  function automatic [6:0] seg7(input [3:0] d);
    seg7 = SEGS[d*7 +: 7];
  endfunction
  reg [6:0] shown;
  always @(*) begin
    shown = seg7(4'hE);
    if (ui_in[7]) shown = seg7(ui_in[3:0]);
  end
  assign uo_out = {1'b0, shown};
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in[6:4], uio_in, 1'b0};
endmodule
