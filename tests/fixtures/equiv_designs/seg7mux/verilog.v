// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// A two-digit multiplexed seven-segment display (round 15 n30): `sel` toggles every clock and
// picks the high or the low nibble of ui_in, a `case` table turns it into segments, and
// uo_out[7] says which digit is lit. `sel` has no value before reset, so the check from
// power-up must not call the segments "x" in the netlist. Must be equivalent.
`default_nettype none
module seg7mux (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  reg sel;
  reg [3:0] nib;
  reg [6:0] seg;
  always @(posedge clk) begin
    if (!rst_n) sel <= 1'b0;
    else sel <= ~sel;
  end
  always @(*) begin
    nib = sel ? ui_in[7:4] : ui_in[3:0];
    seg = 7'd0;
    case (nib)
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
  assign uo_out = {sel, seg};
  assign uio_out = 8'd0;
  assign uio_oe  = 8'd0;
  wire _unused = &{ena, clk, rst_n, ui_in, uio_in, 1'b0};
endmodule
