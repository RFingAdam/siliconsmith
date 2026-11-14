// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A seven-segment decoder, registered every clock with a sync reset (round 14 v62): the hex digit
// on uio_in[3:0] lights the segments a..g of a display on uo_out[6:0] one clock later. The table
// is a `case` of constants; uo_out[7] (the dot) is always off. Must be equivalent.
`default_nettype none
module seg7reg (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // which segments light up for each digit (bit 0 is segment a, bit 6 is segment g)
  reg [6:0] seg;
  always @(*) begin
    case (uio_in[3:0])
      4'h0: seg = 7'b0111111;
      4'h1: seg = 7'b0000110;
      4'h2: seg = 7'b1011011;
      4'h3: seg = 7'b1001111;
      4'h4: seg = 7'b1100110;
      4'h5: seg = 7'b1101101;
      4'h6: seg = 7'b1111101;
      4'h7: seg = 7'b0000111;
      4'h8: seg = 7'b1111111;
      4'h9: seg = 7'b1101111;
      4'ha: seg = 7'b1110111;
      4'hb: seg = 7'b1111100;
      4'hc: seg = 7'b0111001;
      4'hd: seg = 7'b1011110;
      4'he: seg = 7'b1111001;
      default: seg = 7'b1110001;
    endcase
  end

  // the register: all segments off after reset, then the decoded digit every clock
  reg [6:0] q;
  always @(posedge clk) begin
    if (!rst_n) q <= 7'd0;
    else q <= seg;
  end

  assign uo_out  = {1'b0, q};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, ui_in, uio_in[7:4], 1'b0};
endmodule
