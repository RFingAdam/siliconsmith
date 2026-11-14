// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A registered seven-segment table with sync reset; bit 7 is 0 in every entry (round 14 v66).
`default_nettype none
module segsync (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // 8-bit seven-segment table (bit 7 always 0, the dot off), registered every clock with sync reset
  reg [7:0] seg;
  always @(*) begin
    case (ui_in[3:0])
      4'h0: seg = 8'h3f;
      4'h1: seg = 8'h06;
      4'h2: seg = 8'h5b;
      4'h3: seg = 8'h4f;
      4'h4: seg = 8'h66;
      4'h5: seg = 8'h6d;
      4'h6: seg = 8'h7d;
      4'h7: seg = 8'h07;
      4'h8: seg = 8'h7f;
      4'h9: seg = 8'h6f;
      4'ha: seg = 8'h77;
      4'hb: seg = 8'h7c;
      4'hc: seg = 8'h39;
      4'hd: seg = 8'h5e;
      4'he: seg = 8'h79;
      default: seg = 8'h71;
    endcase
  end
  reg [7:0] q;
  always @(posedge clk) begin
    if (!rst_n) q <= 8'd0;
    else q <= seg;
  end
  assign uo_out = q ^ uio_in;
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, ui_in, uio_in, clk, rst_n, 1'b0};
endmodule
