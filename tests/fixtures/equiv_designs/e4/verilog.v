// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Round 16 (round 15 note e4): a CRC-8 step function called with constants in the reset branch
// (crc8(8'hff, 8'ha5)) and with signals otherwise. Its loop counts k down from 7 while k >= 0
// (k is a signed integer), so data[k] stays inside the 8-bit argument: proven in range. Must be
// equivalent.
`default_nettype none
module e4 (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  function automatic [7:0] crc8(input [7:0] crc_in, input [7:0] data);
    reg [7:0] c;
    integer k;
    begin
      c = crc_in;
      for (k = 7; k >= 0; k = k - 1) begin
        if (c[7] ^ data[k]) c = {c[6:0], 1'b0} ^ 8'h07;
        else c = {c[6:0], 1'b0};
      end
      crc8 = c;
    end
  endfunction

  reg [7:0] crc;
  always @(posedge clk) begin
    if (!rst_n) crc <= crc8(8'hff, 8'ha5);
    else if (uio_in[0]) crc <= crc8(crc, ui_in);
  end
  assign uo_out = crc;
  assign uio_out = 8'd0;
  assign uio_oe = 8'd0;
  wire _unused = &{ena, uio_in[7:1], 1'b0};
endmodule
