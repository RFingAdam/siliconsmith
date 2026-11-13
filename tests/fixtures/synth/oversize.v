// SPDX-FileCopyrightText: 2026 Adam Engelbrecht
// SPDX-License-Identifier: AGPL-3.0-or-later

// Far too big for a 1x1 tile on purpose: two 64-bit shift registers loaded serially from
// ui_in, a registered 64x64 product, and the product XOR-reduced to one byte so that synthesis
// cannot optimize the multiplier away.

`default_nettype none

module counter (
    input  wire       clk,
    input  wire       rst_n,
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out
);

  reg [63:0] a;
  reg [63:0] b;
  reg [127:0] product;

  always @(posedge clk) begin
    if (!rst_n) begin
      a <= 64'd0;
      b <= 64'd0;
      product <= 128'd0;
    end else begin
      a <= {a[55:0], ui_in};
      b <= {b[55:0], ~ui_in};
      product <= a * b;
    end
  end

  assign uo_out = product[7:0] ^ product[15:8] ^ product[23:16] ^ product[31:24]
                ^ product[39:32] ^ product[47:40] ^ product[55:48] ^ product[63:56]
                ^ product[71:64] ^ product[79:72] ^ product[87:80] ^ product[95:88]
                ^ product[103:96] ^ product[111:104] ^ product[119:112] ^ product[127:120];

endmodule
